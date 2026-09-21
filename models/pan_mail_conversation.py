# -*- coding: utf-8 -*-
"""
The read side of the conversation view.

This model stores nothing. It is a namespace for the queries the inbox screen
makes, and every one of them is a different `WHERE` over rows that already
exist: `mail.message` for the mail, `mail.activity` for what is next, and the
contact's own tracked-field messages for the events -- the contact's, not yet
the quote's or the ticket's, which is a widening and not a bug fix. The design is in
`docs/plans/conversation-view.md`; how it is built, in
`docs/plans/conversation-view-build.md`.

Two rules hold this layer together.

**Start from the messages, never from the index.** Every query begins with a
search on `mail.message`, so the ORM applies the record rules before we group
anything. `pan.mail.thread.link` is not access controlled: reading it first
would let a thread key betray the existence of a record the user cannot open.
A message on a record somebody cannot read is absent from their result
rather than hidden inside it. Where this file does take `sudo()` it buys a
lookup and never an answer: the thread index behind the record chips, and
the attachments of messages the search already cleared.

**Reads happen here, writes do not.** Replying, marking read, starring and
scheduling a follow-up are Odoo's own methods called on the record itself. A
wrapper would be a second implementation of a decision Odoo already made, and
the reply path (followers, notifications, the routing log) has to behave
exactly as the chatter does. The one write in here, the contact a lead's bare
address becomes when a new mail is addressed to it, is Odoo's own
find-or-create, the same one the chatter's suggestion runs.

The grouping, stated plainly because it is the one approximation in here: a
conversation is the mail on one record, merged with the mail on any other
record that shares a provider thread handle. So a quote and the ticket that
came out of it read as one conversation, which is what people open this screen
to find.
"""
import logging
import re
from datetime import datetime

from odoo import models, api, _
from odoo.exceptions import AccessError
from odoo.fields import Domain
from odoo.addons.mail.tools.discuss import Store
from odoo.tools import email_split, html2plaintext
from odoo.tools.mail import html_sanitize

from .mail_provider_client import FOLDER_INBOX, FOLDER_SENT

_logger = logging.getLogger(__name__)

# What the Files tab draws at most. A tab, not a document archive: past this
# many, the record's own Files box is the screen for it.
FILE_PAGE = 50

# One page. Deliberately small: the list is read, not scrolled through.
DEFAULT_LIMIT = 30

# One page of a live mailbox. Larger than the imported list because it is not
# paged at all: the provider contract's search takes a limit and no offset, so
# this number is the whole of the first answer and the search box is the rest.
LIVE_LIMIT = 50

# The ceiling on what a caller may ask for. `limit` arrives over RPC and every
# query under it materialises rows; a page is a page.
MAX_LIMIT = 200

# Stop counting a folder here and say "99+". Nobody reads the exact number of
# conversations in a busy mailbox, and counting it exactly means aggregating
# every row the reader can see, once per folder, on every click.
COUNT_CAP = 99

# How many rows either step of the link picker hands back. Both steps have a
# search box, so the list is the head of an answer and not the answer: a
# database with four hundred models or ten thousand quotes shows twelve and
# lets the reader type.
MAX_LINK_TARGETS = 12
MAX_LINK_CANDIDATES = 12

# How many thread links door 1 reads to count the threads on one record. The
# number decides one thing -- open the conversation, or let the reader pick --
# and "more than this" and "more than two" lead to the same answer.
THREAD_CAP = 50

# How much of a body the one-line preview looks at. A real mail carries a
# signature, an inline stylesheet and the whole quoted history; the preview is
# 140 characters.
PREVIEW_SOURCE = 8000
# How many mails the chevron unfolds under a conversation. A thread past
# this is one you read in the conversation pane, not one you scan in a list.
THREAD_ROWS = 50
# Where the quoted history starts, as the mail clients people write to us
# from mark it. The same list the conversation pane folds, so the snippet and
# the open message end "what they wrote" at the same place.
QUOTE_START = re.compile(
    r'<blockquote\b|class="[^"]*\b(?:gmail_quote|moz-cite-prefix|OutlookMessageHeader)\b'
    r'|data-o-mail-quote|id="(?:divRplyFwdMsg|appendonsend)"',
    re.IGNORECASE)

# The mailbox list, in the order it is drawn. A mailbox and the three folders
# every mail client has, because that pane is the part of this screen people
# already know how to read. Our own states are not folders and do not belong here;
# they filter the list, one pane to the right.
#
# Drafts is the third, and it is the one folder that is not mail: it lists
# `pan.mail.draft` rows, which nobody but their author can see. It sits in the
# mailbox list anyway, because that is where a mail client keeps unsent mail
# and a Drafts folder somewhere else is a Drafts folder nobody opens.
MAILBOX_FOLDERS = [
    ('inbox', 'Inbox'),
    ('sent', 'Sent'),
    ('drafts', 'Drafts'),
]

# The folder that is read from another table. Every query in this file is a
# `WHERE` over `mail.message`; this one is not, so each entry point says so
# once at the top rather than growing a branch halfway down.
DRAFTS = 'drafts'

# The search bar over the list is Odoo's own, so every state worth filtering a
# folder down to is a `<filter>` in `views/pan_mail_conversation_views.xml`
# rather than a list here: it produces a domain over `mail.message`, and a
# domain is the whole contract between that bar and this file.
#
# `pan_mail_ungrouped` is the one thing a domain cannot say. Mail linked to
# nothing is not one conversation, so the filter that asks for it carries that
# key in its context and the client passes it on as `ungrouped`.
UNGROUPED_KEY = 'pan_mail_ungrouped'

KINDS = {value: 'folder' for value, _label in MAILBOX_FOLDERS}

# The matcher's rule names, in words. The screen shows why a mail was not
# filed, and `subject_participants` is not why anything happened.
ROUTING_RULES = {
    'odoo_headers': 'Our own headers on a reply',
    'references': 'The reply headers of the thread',
    'thread_link': 'A thread already filed on this record',
    'thread_link_legacy': 'An older thread id for this record',
    'subject_participants': 'The same subject and the same people',
}

# The chatter posts that are correspondence once they have gone out. A reply
# written on this screen is Odoo's own chatter post, so it is a `comment`, or
# an `auto_comment` when a template wrote it. Nothing else a chatter produces
# is a mail somebody typed.
SENT_TYPES = ('comment', 'auto_comment')


class PanMailConversation(models.AbstractModel):
    """Queries behind the conversation view. No table, no stored fact."""

    _name = 'pan.mail.conversation'
    _description = 'Conversation View'

    # ------------------------------------------------------------------
    # The domain every folder is built from
    # ------------------------------------------------------------------

    def _mail_domain(self):
        """What this screen counts as correspondence.

        `message_type = 'email'` is the mail the sync imported, and it is what
        keeps internal notes out of the list and out of the Mail tab. They are
        one tab away, in Everything, which is where the chatter's history went
        when the record pane lost it.

        It is not the whole answer, because a reply written here never gets
        that type: it is a chatter post, and Odoo types every chatter post
        `comment` (or `auto_comment` for a template). The only column that
        says it left the building is `x_direction`, which `mail.mail` stamps
        once the provider accepted it. Asking for the type alone hid every
        answer this module sent from the conversation it was sent in, from the
        Sent folder, and from every count on this screen.

        `is_internal` is what keeps the notes out of the second branch: a note
        posted to followers is mailed to them, so it is outgoing too, and it
        is not correspondence.
        """
        return ['|', ('message_type', '=', 'email'),
                '&', '&', ('x_direction', '=', 'outgoing'),
                ('message_type', 'in', SENT_TYPES),
                ('is_internal', '=', False)]

    def _base_domain(self, mailbox_id=None, partner_id=None, domain=None,
                     in_a_mailbox=False):
        """Mail this user may read, optionally narrowed to one mailbox.

        `domain` is the search bar's, and it is the only thing the bar hands
        over: a facet, a typed word and a filter all arrive here as clauses on
        `mail.message`.

        `in_a_mailbox` is the All mailboxes folder asking for what its own
        label promises: the mail that is *in* a mailbox, all of them at once.
        Without it the query also returns mail no mailbox owns -- what the
        chatter sent before this module was installed, and Odoo's own -- which
        under a row that says "All mailboxes" is neither true nor useful.

        It is off by default, because the other caller of "no mailbox" wants
        exactly the opposite: door 1 opens the Inbox on one record's mail
        wherever it arrived, and mail this module never handled is still that
        record's correspondence.
        """
        base = self._mail_domain()
        if mailbox_id:
            base.append(('x_mailbox_id', '=', mailbox_id))
        elif in_a_mailbox:
            base.append(('x_mailbox_id', '!=', False))
        if partner_id:
            partner = self.env['res.partner'].browse(partner_id)
            # The company, not the person: jan@acme and inkoop@acme are one
            # correspondence, and `commercial_partner_id` is how Odoo says so.
            # Written as a dotted path so the ORM emits a subquery. Fetching
            # the sibling contacts first pastes a few thousand ids into every
            # clause of every query a large customer's timeline makes.
            company = partner.commercial_partner_id or partner
            base += ['|',
                     ('author_id.commercial_partner_id', '=', company.id),
                     ('partner_ids.commercial_partner_id', '=', company.id)]
        if domain:
            # What the search bar asked for, over `mail.message`. Parsed
            # rather than pasted: `Domain` refuses anything that is not a
            # domain, and what a domain may ask of this model is what a
            # `search_read` from the same session may ask of it anyway.
            base += list(Domain(domain))
        return base

    def _folder_domain(self, folder):
        """The extra clauses the chosen folder adds to the grouping query.

        Sent is "this conversation was written in", not "we spoke last": a
        thread the customer answered is still one you sent in, which is what
        the word means in the mail client open next to this one.
        """
        if folder == 'sent':
            return [('x_direction', '=', 'outgoing')]
        return []

    def _conversation_domain(self, model, res_id, message_id=None,
                             mailbox_id=None):
        """The mail of one conversation, however the caller named it.

        Reading it and marking it read have to mean the same set of messages,
        so they ask the same method rather than each building the clauses
        again.
        """
        base = self._base_domain(mailbox_id)
        if model:
            return base + [('model', '=', model), ('res_id', '=', res_id)]
        if message_id:
            return base + [('id', '=', int(message_id))]
        # `= 0` does not match a NULL res_id, so a conversation linked to
        # nothing, asked for by key rather than by message, has to say False.
        return base + [('model', '=', False), ('res_id', '=', False)]

    # ------------------------------------------------------------------
    # Public read methods, the whole API the client has
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Who may call this, and how much they may ask for
    # ------------------------------------------------------------------

    def _check_caller(self):
        """This layer is for people who read a mailbox.

        The menu carries the group, but a menu is not an ACL: the action is
        reachable by URL and every method here is reachable over `call_kw`.
        `security/pan_mail_pro_security.xml` already records what that costs,
        in the release where a group on a menu was mistaken for a rule.
        """
        if not self.env.user.has_group('pan_mail_pro.group_mail_mailbox_manager'):
            raise AccessError(_("Mail Pro's inbox is for mailbox managers."))

    def _page(self, limit, offset, default=DEFAULT_LIMIT):
        """Clamp what the client asked for.

        `limit` arrives over RPC, and the queries underneath it materialise
        every row the reader may see. A page is a page.
        """
        try:
            limit = int(limit or default)
        except (TypeError, ValueError):
            limit = default
        try:
            offset = int(offset or 0)
        except (TypeError, ValueError):
            offset = 0
        return max(min(limit, MAX_LIMIT), 1), max(offset, 0)

    # ------------------------------------------------------------------
    # Public read methods, the whole API the client has
    # ------------------------------------------------------------------

    @api.model
    def failure_remedy(self):
        """What to do about the read that just failed, in one sentence.

        The banner can repeat what the server said; it cannot know what to do
        about it, and "Could not load your conversations" sends the reader to
        a log for a line the browser already had. One cause is common enough
        to be worth naming: code deployed without the module being upgraded.
        The registry then holds fields the database has no columns for, every
        query in here fails on a missing column, and nothing on screen says
        so. Cloudpepper restarts on a push and never upgrades (issue #134),
        so this is the normal shape of an inbox that broke by itself.

        Nothing here reads a `pan_mail_*` table. This method has to answer on
        exactly the databases where those are what is broken.
        """
        self._check_caller()
        module = self.env['ir.module.module'].sudo().search(
            [('name', '=', 'pan_mail_pro')], limit=1)
        # Odoo's two version fields are named the other way round from how
        # they read: `installed_version` is computed from the manifest on
        # disk, `latest_version` is the string the last upgrade wrote into
        # the database. The gap between them is the whole diagnosis.
        on_disk = module.installed_version or ''
        in_database = module.latest_version or ''
        if module.state == 'to upgrade' or (on_disk and in_database and on_disk != in_database):
            return _(
                "Mail Pro %(on_disk)s is on the server, this database still "
                "runs %(in_database)s. Open Apps, search for Mail Pro and "
                "press Upgrade.",
                on_disk=on_disk, in_database=in_database,
            )
        # Every other failure: the line the server gave is what there is. A
        # remedy we cannot name is worse than none, because it sends the
        # reader somewhere that is not where the problem is.
        return ''

    @api.model
    def inbox_search_view_id(self):
        """The search view the Inbox's search bar is built from.

        The bar is Odoo's own `SearchBar` over `mail.message`, and a search
        bar needs the id of a view rather than a name. Asked for here rather
        than pinned in the client action's context so an inherited view is all
        it takes to add a filter of your own.
        """
        self._check_caller()
        return self.env['ir.model.data']._xmlid_to_res_id(
            'pan_mail_pro.view_pan_mail_inbox_search')

    @api.model
    def folder_counts(self, mailbox_id=None, partner_id=None,
                      domain=None, search=None, ungrouped=False,
                      in_a_mailbox=False):
        """The numbers on the mailbox list, one per folder.

        Counted on every read, capped at `COUNT_CAP`. A stored counter would be
        one more fact that can disagree with the messages; an exact count means
        aggregating every row the reader can see, once per entry, on every
        click. The cap costs a "+" on the label and saves the scan.

        It takes the same `partner_id`, `domain` and `search` the list takes,
        so the two panes always describe the same mail: narrow the search and
        the numbers beside Inbox, Sent and Drafts narrow with it.

        The search bar's filters are counted nowhere. Odoo's own filter menu
        carries no numbers either, and a count per filter is a grouping query
        per filter on every click.
        """
        self._check_caller()
        base = self._base_domain(mailbox_id, partner_id, domain, in_a_mailbox)
        return [self._count_entry(base, value, label,
                                  self._folder_domain(value), ungrouped)
                if value != DRAFTS
                else self._draft_entry(value, label, mailbox_id, search)
                for value, label in MAILBOX_FOLDERS]

    def _count_entry(self, domain, value, label, extra, ungrouped=False):
        """One number beside a folder in the mailbox list, capped."""
        groups = self.env['mail.message']._read_group(
            domain + extra, groupby=['model', 'res_id'],
            aggregates=['__count', 'date:max'],
            order='date:max DESC, model ASC, res_id ASC',
            limit=COUNT_CAP + 1,
        )
        if ungrouped:
            # The list is not grouping either, so the number has to count
            # what the list will show: messages, not conversations.
            total = sum(count for _model, _res_id, count, _date in groups)
        else:
            total = len(groups)
        return {
            'id': value,
            'name': label,
            'kind': KINDS[value],
            'count': min(total, COUNT_CAP),
            'capped': total > COUNT_CAP,
        }

    def _draft_entry(self, value, label, mailbox_id, search):
        """The Drafts number, counted in the table that holds them."""
        counted = self.env['pan.mail.draft'].folder_count(
            mailbox_id=mailbox_id, search=search, cap=COUNT_CAP)
        return {
            'id': value,
            'name': label,
            'kind': KINDS[value],
            'count': counted['count'],
            'capped': counted['capped'],
        }

    @api.model
    def search_conversations(self, mailbox_id=None, folder='inbox',
                             partner_id=None, domain=None, search=None,
                             ungrouped=False,
                             record_model=None, record_id=None,
                             limit=DEFAULT_LIMIT, offset=0, in_a_mailbox=False):
        """One page of conversations, newest first.

        Two dimensions: the folder from the mailbox list, and whatever the
        search bar asks on top of it. A folder is a place mail is, a search is
        a question about it, and the screen keeps them apart because the
        mailbox list is the part people already know how to read.

        `domain` is the search bar's, over `mail.message`. `ungrouped` is the
        one thing it cannot say: the filter that asks for mail linked to
        nothing carries `pan_mail_ungrouped` in its context, and the client
        passes it on here. `search` is the words the reader typed, which is
        the only part of that bar Drafts can use: those live in a table of
        their own, so a domain over `mail.message` means nothing to them.

        `record_model` / `record_id` narrow the list to one record. That is
        door 1 arriving from a chatter: the reader came from a record rather
        than from a mailbox, so the list says what is on it and the mailbox
        is not the question.

        A fixed number of queries, whatever the page size: the grouping, the
        newest message of each group, the message counts, and the display
        names of the records. The newest messages come back as one
        recordset on purpose, so the ORM prefetches their authors, mailboxes
        and document names for the whole page instead of once per row.
        """
        self._check_caller()
        limit, offset = self._page(limit, offset)
        if folder == DRAFTS:
            # Another table, the same row shape. A draft is on a record from
            # the moment it is saved, so it needs no grouping: there is one
            # draft per row and it already knows where it belongs.
            #
            # It takes `search` and not `domain`: the search bar's domain is
            # over `mail.message`, and a draft is not one. What carries across
            # the two tables is the words somebody typed, so that is what the
            # client sends beside the domain.
            return self.env['pan.mail.draft'].folder_rows(
                mailbox_id=mailbox_id, search=search,
                record_model=record_model, record_id=record_id,
                limit=limit, offset=offset)
        base = self._base_domain(mailbox_id, partner_id, domain, in_a_mailbox)
        if record_model and record_id:
            base = base + [('model', '=', record_model),
                           ('res_id', '=', int(record_id))]
        narrowed = base + self._folder_domain(folder)

        # Mail nobody filed is not one conversation. Grouping it on
        # (model, res_id) would collapse every unmatched message in the
        # database into a single row belonging to nobody, which is the exact
        # opposite of the state that filter exists to make reviewable.
        if ungrouped:
            return self._ungrouped_rows(narrowed, limit, offset)

        Message = self.env['mail.message']

        groups = Message._read_group(
            narrowed,
            groupby=['model', 'res_id'],
            aggregates=['__count', 'date:max'],
            # The tiebreaker is not decoration: LIMIT/OFFSET over an ambiguous
            # ORDER BY may return the same conversation on two pages and never
            # return another one.
            order='date:max DESC, model ASC, res_id ASC',
            limit=limit,
            offset=offset,
        )
        if not groups:
            return []

        # The newest message and the message count come from the *base*
        # domain, never from the narrowed one: the row describes the whole
        # conversation, not the folder's slice of it.
        newest = self._newest_per_group(base, groups)
        if not newest:
            return []

        counts = self._counts_per_group(base, newest)
        return [
            self._conversation_row(
                message, counts.get((message.model, message.res_id), 1))
            for message in newest
        ]

    @api.model
    def read_conversation(self, model, res_id, mailbox_id=None,
                          message_id=None, limit=50, offset=0, scope='mail'):
        """One conversation, and everything the four tabs over it draw.

        `messages` is the thread, newest first. `records` is the chip row:
        every record this thread touched, newest first. `rejected` is what the
        matcher considered and turned down, and it is only looked up when
        nothing was filed, because that is the only case anybody wants to read
        it. `files` and `activities` are the two other lists.

        `scope` is which reading of the thread the reader asked for: `mail` is
        the correspondence and nothing else, `all` interleaves the internal
        notes and the record's own events. It changes `messages` and nothing
        else -- a tab count that moves when you open another tab reads as a
        bug -- so `files` and `activities` are the same on every tab.

        A conversation linked to nothing has no record to key on, so it is
        addressed by `message_id` instead.
        """
        self._check_caller()
        limit, offset = self._page(limit, offset, default=50)
        Message = self.env['mail.message']
        domain = Domain(self._conversation_domain(
            model, res_id, message_id, mailbox_id))
        if model and scope == 'all':
            # A note and a stage change belong to the record, not to a
            # mailbox, and carry no direction. Running them through the
            # correspondence clauses would empty the tab that exists to
            # show them.
            domain = Domain.OR([domain, Domain(
                [('model', '=', model), ('res_id', '=', res_id),
                 ('message_type', 'in', ('comment', 'notification'))])])

        # Newest first, on the screen as well as in the query. The message
        # you came for is the last one, so it belongs where the eye lands and
        # not at the far end of a thread nobody scrolls to. It is also the
        # one the pane opens, and an open message below fifty collapsed
        # headers is an open message nobody sees.
        messages = Message.search(
            domain, order='date desc, id desc', limit=limit, offset=offset,
        )
        records = self._records_for(messages)
        return {
            'messages': [self._message_row(m) for m in messages],
            'records': records,
            'rejected': [] if model else self._rejected_for(messages),
            'suggestion': self._suggestion_for(messages),
            'files': self._files_for(model, res_id, messages),
            'activities': self._activities_for(records),
            # Your own unsent answers on this record, above the thread. Yours
            # only: the record rule on `pan.mail.draft` is what decides that,
            # and nothing here lifts it.
            'drafts': self.env['pan.mail.draft'].rows_for(model, res_id),
        }

    @api.model
    def conversation_messages(self, model, res_id, message_id=None,
                              mailbox_id=None, limit=THREAD_ROWS, offset=0):
        """The mails inside one conversation, one line each, newest first.

        What the chevron in the conversation list unfolds. It is the same set of
        messages `read_conversation` returns for the `mail` tab, and deliberately
        not the same rows: a line in a list needs a sender, a date and a
        snippet, and a body per message would send the whole thread over the
        wire to draw twenty lines of text. Asking `read_conversation` and
        throwing the bodies away is the version of this that looks like reuse
        and costs a thread's worth of HTML per chevron.

        No `scope`: the list is correspondence. An internal note is not a mail
        the conversation had, and the tab strip over the open conversation is
        where that reading lives.
        """
        self._check_caller()
        limit, offset = self._page(limit, offset, default=THREAD_ROWS)
        messages = self.env['mail.message'].search(
            self._conversation_domain(model, res_id, message_id, mailbox_id),
            order='date desc, id desc', limit=limit, offset=offset,
        )
        return [self._thread_row(message) for message in messages]

    @api.model
    def set_read(self, model, res_id, read=True, message_id=None,
                 mailbox_id=None):
        """Mark one conversation read or unread, everywhere it is recorded.

        Three writes, in the order that survives a failure: the mirror in
        Odoo, then the reader's own Odoo Inbox rows, then the provider. The
        first is what the screen draws, the last is best effort by design (see
        `pan.mail.mailbox.push_read_state`), and a refresh settles any
        disagreement in favour of the provider a minute later.

        Per conversation, because reading is: nobody reads the fourth message
        of a thread and not the fifth.

        Returns:
            dict: `read` as it now stands and `count`, the messages touched.
        """
        self._check_caller()
        # Searched as the caller, so a conversation they may not read is a
        # conversation they cannot mark. Odoo's own rules over `mail.message`
        # do that work and this must not step around them.
        messages = self.env['mail.message'].search(
            self._conversation_domain(model, res_id, message_id, mailbox_id))
        if not messages:
            return {'read': bool(read), 'count': 0}

        # Only what actually moves. The Inbox calls this every time a
        # conversation is opened, and a conversation that was already read
        # must not cost a provider call for saying so again.
        changing = messages.filtered(lambda m: m.x_is_read != bool(read))

        # Written with sudo: read state is a fact about the mailbox, not about
        # the document, and a reader with no write access to somebody's sale
        # order may still have read their mail. The search above is what
        # decided they may touch these messages at all.
        changing.sudo().write({'x_is_read': bool(read)})

        if read:
            # Your own Odoo Inbox rows for these messages, and nobody else's:
            # `set_message_done` works from `env.user`, and it sends the bus
            # message that makes the bell count down while you read. Asked of
            # every message, not only the ones that moved -- the notification
            # row and the mailbox's read state are two different facts, and
            # the bell can still be ringing for a mail the mailbox calls read.
            messages.set_message_done()

        for mailbox in changing.mapped('x_mailbox_id'):
            mailbox.push_read_state(
                changing.filtered(lambda m: m.x_mailbox_id == mailbox),
                read=bool(read))
        return {'read': bool(read), 'count': len(changing)}

    @api.model
    def refresh_read_state(self, mailbox_id=None):
        """Ask the provider which mail is unread, for one mailbox or all.

        The Inbox calls this when it opens a mailbox. It is throttled per
        mailbox (`READ_STATE_TTL`), so clicking between folders costs one
        provider call a minute and not one a click, and it never raises: a
        provider that is unreachable leaves the mirror as it was.

        Returns:
            int: how many messages changed, so the client knows whether the
                list it already drew is now wrong.
        """
        self._check_caller()
        Mailbox = self.env['pan.mail.mailbox']
        mailboxes = (Mailbox.browse(int(mailbox_id)).exists() if mailbox_id
                     else Mailbox.search([]))
        return sum(mailbox.refresh_read_state() for mailbox in mailboxes)

    @api.model
    def record_conversations(self, model, res_id):
        """What the chatter's door needs: is there mail, and is it all here.

        `elsewhere` is the reason the extra line exists. When it is zero the
        client draws nothing, because a line that says "everything is already
        on this page" is noise.

        `threads` and `conversations` count two different things and the
        button needs the first. `threads` is how many email threads are filed
        *on this record*, which is what decides whether Open in mail can open
        one; `conversations` is how many records the recent mail touched,
        which is what the messages-elsewhere line talks about.
        """
        self._check_caller()
        Message = self.env['mail.message']
        base = self._mail_domain() + [
            ('model', '=', model),
            ('res_id', '=', res_id),
        ]
        here_count = Message.search_count(base)
        if not here_count:
            return {'conversations': 0, 'threads': 0, 'here': 0, 'elsewhere': 0,
                    'partner_id': False, 'partner_name': ''}

        # Enough of the thread to find the records it touched, not all of it.
        # A record carrying ten thousand mails still has to open in one query.
        here = Message.search(base, order='date desc, id desc', limit=50)
        records = self._records_for(here)

        # A contact chip would drag that contact's entire correspondence into
        # the count, which is not what "the rest of this conversation" means.
        others = [row for row in records
                  if (row['model'], row['res_id']) != (model, res_id)
                  and row['model'] != 'res.partner']
        elsewhere = self._count_on_records(others) if others else 0

        return {
            'conversations': len(records) or 1,
            'threads': self._thread_count(model, res_id),
            'here': here_count,
            'elsewhere': elsewhere,
            'partner_id': self._correspondent(here).id or False,
            'partner_name': self._correspondent(here).display_name or '',
        }

    def _thread_count(self, model, res_id):
        """How many email threads are filed on this record. At least one.

        Counted on the References root, the key that means the same thing in
        every mailbox: one conversation seen by sales@ and support@ is one
        thread, not two. `pan.mail.thread.link` writes a row per handle a
        conversation carries, so the provider's own handle is skipped here --
        it is the same conversation under its mailbox-local name. A provider
        that mints no handle of its own (IMAP) stores the root as its
        provider key, and those rows are the fallback when there is no `rfc`
        row at all.

        `sudo` buys the lookup and not the answer, as everywhere in this
        file: the caller already read messages on this record, so the count
        says nothing the reader could not see by scrolling the chatter.
        """
        links = self.env['pan.mail.thread.link'].sudo().search(
            [('model', '=', model), ('res_id', '=', res_id)], limit=THREAD_CAP)
        keys = {link.thread_id for link in links if link.key_type == 'rfc'}
        if not keys:
            keys = {link.thread_id for link in links}
        return len(keys) or 1

    @api.model
    def customer_timeline(self, partner_id, kinds=None, limit=40, offset=0):
        """One axis for a customer: what is next, then what happened.

        `next` is Salesforce's future half and Odoo's `mail.activity`
        unchanged. `items` is the past, merged from the kinds asked for.

        Both halves of the merge are fetched to the same depth and the slice
        happens once, after the sort. Paging the two lists separately serves
        page one's events again on page two.
        """
        self._check_caller()
        limit, offset = self._page(limit, offset, default=40)
        kinds = kinds or ['mail', 'activity', 'event']
        partner = self.env['res.partner'].browse(partner_id)
        company = partner.commercial_partner_id or partner
        depth = limit + offset

        merged = []
        if 'mail' in kinds:
            merged += [('mail', message) for message in self.env['mail.message'].search(
                self._base_domain(partner_id=partner_id),
                order='date desc, id desc', limit=depth,
            )]
        if 'event' in kinds:
            merged += [('event', message) for message in self.env['mail.message'].search([
                ('message_type', '=', 'notification'),
                ('model', '=', 'res.partner'),
                ('res_id', '=', company.id),
            ], order='date desc, id desc', limit=depth)]

        merged.sort(key=lambda pair: (pair[1].date or datetime.min, pair[1].id),
                    reverse=True)
        page = merged[offset:offset + limit]

        items = []
        for kind, message in page:
            if kind == 'mail':
                row = self._message_row(message)
                row['kind'] = 'mail'
            else:
                row = {
                    'kind': 'event',
                    'id': message.id,
                    'date': message.date,
                    'subject': message.subject or _('Update'),
                    'author': message.author_id.display_name or '',
                    'model': message.model,
                    'res_id': message.res_id,
                }
            items.append(row)

        return {
            'next': self._next_for(company) if 'activity' in kinds else [],
            'items': items,
            'has_more': len(merged) > offset + limit,
        }

    # ------------------------------------------------------------------
    # Your own mailbox, read from the provider
    #
    # The design is in `docs/plans/personal-mailbox.md`. Two rules, and they
    # are the whole of why this is allowed to exist:
    #
    # **Nothing is stored.** These methods read the mailbox through the client
    # contract and return rows. There is no mirror table, no flag of ours on a
    # provider message and no cursor, so there is nothing to keep correct and
    # nothing that outlives the request. The privacy boundary does not move
    # because no mail crosses it.
    #
    # **Ownership is the access rule.** A group says "may read a mailbox";
    # the question here is "may read *this* mailbox in full", and only its
    # owner may. A shared mailbox has no owner and is refused outright, which
    # is also the answer to "can a colleague read my mail this way": no, and
    # not because of a menu.
    # ------------------------------------------------------------------

    def _own_mailbox(self, mailbox_id):
        """The mailbox, if it is the caller's own personal one.

        Deliberately not `_is_sendable_by`: that one lets anybody send through
        the notification mailbox, which is right for sending and would be a
        hole here.
        """
        self._check_caller()
        try:
            mailbox = self.env['pan.mail.mailbox'].browse(int(mailbox_id)).exists()
        except (TypeError, ValueError):
            mailbox = self.env['pan.mail.mailbox'].browse()
        if not mailbox:
            raise AccessError(_('No such mailbox.'))
        if (mailbox.mailbox_type != 'personal'
                or mailbox.owner_user_id != self.env.user):
            raise AccessError(
                _('Reading a mailbox in full is for its own owner. A shared '
                  'mailbox is read through what the sync imported.'))
        return mailbox

    @api.model
    def live_mailboxes(self):
        """The mailboxes this user may read in full: their own, if any."""
        self._check_caller()
        mailboxes = self.env['pan.mail.mailbox'].search([
            ('mailbox_type', '=', 'personal'),
            ('owner_user_id', '=', self.env.user.id),
        ])
        return [{'id': m.id, 'email': m.email}
                for m in mailboxes if m._has_working_credentials()]

    @api.model
    def live_messages(self, mailbox_id, folder=FOLDER_INBOX, linked=None,
                      search=None, limit=LIVE_LIMIT):
        """One page of your own mailbox, newest first, straight from the provider.

        `linked` is the filter the whole feature is for: `False` asks for the
        mail Odoo does not have, `True` for the mail it does, `None` for the
        mailbox as it is. It is applied after the provider answers, because no
        provider can be asked "is this in Odoo" -- which also means a page of
        50 filtered down may show fewer than 50 rows, and says so with
        `scanned`.

        Not paged: `search_messages` takes a limit and no offset. Older mail
        is a search term rather than a scroll, which is how anybody finds a
        mail from March anyway.
        """
        mailbox = self._own_mailbox(mailbox_id)
        if folder not in (FOLDER_INBOX, FOLDER_SENT):
            raise AccessError(_('That folder is not one this screen reads.'))
        limit, _offset = self._page(limit, 0, default=LIVE_LIMIT)
        client = mailbox._get_client()
        account = client.resolve_receiving_account(mailbox)
        if not account.connected:
            return {'rows': [], 'scanned': 0, 'connected': False}
        try:
            messages = client.search_messages(
                account=account, mailbox=mailbox, folder=folder,
                query=search or None, limit=limit,
            )
        except Exception:
            # A provider that cannot be reached right now -- an expired grant,
            # a network that is down, a mailbox that moved -- is this folder
            # being unavailable, not this screen breaking. The imported
            # folders next to it still read, because they never leave the
            # database.
            _logger.warning(
                "[Mail Pro] Live read failed for mailbox %s", mailbox.id,
                exc_info=True)
            return {'rows': [], 'scanned': 0, 'connected': False}
        links = self._links_for_live(messages)
        rows = []
        for message in messages:
            link = links.get(message.get('message_id') or '')
            if linked is not None and bool(link) != bool(linked):
                continue
            rows.append(self._live_row(mailbox, message, link))
        return {'rows': rows, 'scanned': len(messages), 'connected': True}

    @api.model
    def read_live_message(self, mailbox_id, provider_message_id):
        """One live message in full, for the reading pane.

        A read and nothing else: it does not mark the message seen. Reading
        mail here must not change what the mail client next to this screen
        shows, and a seen flag written from a preview is the classic way to
        lose an email.
        """
        mailbox = self._own_mailbox(mailbox_id)
        client = mailbox._get_client()
        account = client.resolve_receiving_account(mailbox)
        message = client.get_message(
            account=account, mailbox=mailbox,
            provider_message_id=provider_message_id,
        )
        if not message:
            raise AccessError(_('That message is no longer in this mailbox.'))
        link = self._links_for_live([message]).get(message.get('message_id') or '')
        row = self._live_row(mailbox, message, link)
        # Sanitized here, and this is the one body in the module that is not.
        # Every other body on this screen reached `mail.message` through
        # `message_post`, where the Html field sanitizes it on write; this one
        # comes straight off the provider and is rendered in an Odoo session.
        # Same call, one step earlier.
        row['body'] = html_sanitize(message.get('body_html') or '')
        row['linked_record'] = link or False
        row['to'] = [a.get('email') for a in (message.get('to') or []) if a.get('email')]
        return row

    @api.model
    def import_live_message(self, mailbox_id, provider_message_id):
        """File one live message in Odoo, and say where it landed.

        This is the moment a private read becomes Odoo data, so it is an
        explicit act with its own button and never a side effect of opening a
        message. It runs the ordinary fetcher with `pan_mail_force_import`,
        which lifts the sync-level and internal-domain filters and lifts
        neither the duplicate guard nor the contact block list.

        The fetcher runs as the system, the way the cron runs it: filing a
        mail creates a contact, posts on a record and writes a routing log,
        and a person reading their own mailbox has rights to none of those.
        The authorisation is `_own_mailbox` above -- you may only file a
        message that is in a mailbox you own -- and what happens next is the
        import this module already does, triggered by hand instead of by the
        clock. The gates run either way, so the block list still holds.
        """
        mailbox = self._own_mailbox(mailbox_id)
        client = mailbox._get_client()
        account = client.resolve_receiving_account(mailbox)
        message = client.get_message(
            account=account, mailbox=mailbox,
            provider_message_id=provider_message_id,
        )
        if not message:
            raise AccessError(_('That message is no longer in this mailbox.'))
        folder = FOLDER_SENT if self._is_own_address(mailbox, message) else FOLDER_INBOX
        imported = self.env['pan.mail.fetcher'].sudo().with_context(
            pan_mail_force_import=True)._process_message(mailbox, message, folder)
        link = self._links_for_live([message]).get(message.get('message_id') or '')
        return {'imported': bool(imported), 'linked': link or False}

    # ------------------------------------------------------------------
    # Live helpers
    # ------------------------------------------------------------------

    def _is_own_address(self, mailbox, message):
        """Did this mailbox write the message? Which decides Inbox or Sent."""
        sender = (message.get('from') or {}).get('email') or ''
        return sender.strip().lower() == (mailbox.email or '').strip().lower()

    def _links_for_live(self, messages):
        """Message-ID -> the record it is filed on, for a page of live rows.

        Two places to look, for the reason `pan.mail.matcher._resolve_message_id`
        gives: Odoo's own `message_id`, which is where `message_post` puts the
        id on import, and the ref index, which carries every *other* id a
        message was seen under -- the one the provider minted when we sent it
        included. The index alone answers "no" for most imported mail, because
        `_index_message` writes nothing when the two agree, which is the normal
        case. Batched rather than per row: this runs once per page.

        Both reads are sudo, so they can answer for a record the reader may not
        open. That is deliberate and bounded: the answer is "yes, Odoo has
        this", never the record's name, which is filled in below only for
        records the reader can actually read. Telling somebody their own email
        is already filed is not a leak; telling them what it is filed on can be.
        """
        wanted = [m.get('message_id').strip()
                  for m in messages if m.get('message_id')]
        if not wanted:
            return {}
        found = {}
        own = self.env['mail.message'].sudo().search(
            [('message_id', 'in', wanted)], order='id desc')
        for message in own:
            if not message.model or not message.res_id:
                continue
            found.setdefault(message.message_id, (message.model, message.res_id))
        missing = [message_id for message_id in wanted if message_id not in found]
        refs = self.env['pan.mail.message.ref'].sudo().search(
            [('message_id', 'in', missing)]) if missing else []
        for ref in refs:
            message = ref.mail_message_id
            if not message.model or not message.res_id:
                continue
            found.setdefault(ref.message_id, (message.model, message.res_id))
        names = self._readable_names(found.values())
        return {
            message_id: {
                'model': model,
                'res_id': res_id,
                'name': names.get((model, res_id)) or '',
            }
            for message_id, (model, res_id) in found.items()
        }

    def _readable_names(self, pairs):
        """Display names for the records in this page the reader may open."""
        names = {}
        by_model = {}
        for model, res_id in pairs:
            by_model.setdefault(model, set()).add(res_id)
        for model, res_ids in by_model.items():
            if model not in self.env:
                continue
            records = self.env[model].browse(sorted(res_ids)).exists()
            for record in records._filtered_access('read'):
                names[(model, record.id)] = record.display_name
        return names

    def _live_row(self, mailbox, message, link):
        """One row of the live list, in the shape the list already draws.

        Deliberately the same keys as `_conversation_row`: the list is one
        component, and a second row shape would be a second template, a second
        empty state and a second way to be selected. What a live row adds is
        `live_id` -- the provider's own handle, which is the only way back to
        this message -- and `linked`, which is the whole point of the screen.
        A live row has no `message_id` because Odoo has no message for it,
        which is also how the client tells the two apart.
        """
        sender = message.get('from') or {}
        return {
            'model': (link or {}).get('model') or False,
            'res_id': (link or {}).get('res_id') or 0,
            'message_id': False,
            'live': True,
            'live_id': message.get('provider_message_id'),
            'linked': bool(link),
            'subject': message.get('subject') or _('(no subject)'),
            'preview': self._live_preview(message),
            'correspondent': sender.get('name') or sender.get('email') or '',
            'email': sender.get('email') or '',
            'partner_id': False,
            'date': message.get('date') or False,
            'count': 1,
            'record_name': (link or {}).get('name') or '',
            'unread': not message.get('is_read'),
            'mailbox': mailbox.email or '',
        }

    def _live_preview(self, message):
        """The one-line snippet, from whatever the list form gave us."""
        body = (message.get('body_html') or '')[:PREVIEW_SOURCE]
        if not body:
            return ''
        cut = QUOTE_START.search(body)
        if cut:
            body = body[:cut.start()]
        text = html2plaintext(body) if message.get('body_is_html') else body
        return ' '.join(text.split())[:140]

    # ------------------------------------------------------------------
    # Batch helpers: one query for the page, never one per row
    # ------------------------------------------------------------------

    def _ungrouped_rows(self, domain, limit, offset):
        """One row per message, because that is what the reader asked for.

        Nothing groups these: they are the mails the matcher could not place,
        and the whole point of that filter is to look at them one at a time.
        The domain is the search bar's, so a reader who combines that filter
        with another one gets every matching message on its own row -- the
        one case this drops rather than handles, because a page that is half
        conversations and half messages cannot be paged.
        """
        messages = self.env['mail.message'].search(
            domain, order='date desc, id desc', limit=limit, offset=offset,
        )
        rows = []
        for message in messages:
            row = self._conversation_row(message, 1)
            row['message_id'] = message.id
            rows.append(row)
        return rows

    def _newest_per_group(self, domain, groups):
        """The newest message of each group, as one recordset.

        One query. The groups already know their newest date, so this asks for
        the messages at exactly those dates and keeps the last one per group,
        which also settles two mails sharing a timestamp to the second. One
        recordset rather than thirty is what lets the ORM prefetch the authors
        and mailboxes for the whole page.
        """
        Message = self.env['mail.message']
        if not groups:
            return Message.browse()
        keys = Domain.OR(
            Domain([('model', '=', model or False),
                    ('res_id', '=', res_id or False),
                    ('date', '=', date)])
            for model, res_id, _count, date in groups
        )
        candidates = Message.search(Domain.AND([Domain(domain), keys]),
                                    order='date desc, id desc')
        chosen = {}
        for message in candidates:
            chosen.setdefault((message.model, message.res_id), message.id)

        # Keep the order the grouping query decided.
        ordered = [chosen[(model, res_id)]
                   for model, res_id, _count, _date in groups
                   if (model, res_id) in chosen]
        return Message.browse(ordered)

    def _counts_per_group(self, base, messages):
        """How many messages each of these conversations holds, in one query.

        Counted over the base domain, so "3 messages" is the size of the
        conversation rather than the size of the folder's slice of it.
        """
        if not messages:
            return {}
        keys = Domain.OR(
            Domain([('model', '=', message.model or False),
                    ('res_id', '=', message.res_id or False)])
            for message in messages
        )
        groups = self.env['mail.message']._read_group(
            Domain.AND([Domain(base), keys]),
            groupby=['model', 'res_id'], aggregates=['__count'],
        )
        return {(model, res_id): count for model, res_id, count in groups}

    def _count_on_records(self, records):
        """How many emails sit on these records, in one grouped query."""
        wanted = {(row['model'], row['res_id']) for row in records}
        groups = self.env['mail.message']._read_group(
            self._mail_domain() + [
                ('model', 'in', list({row['model'] for row in records})),
                ('res_id', 'in', list({row['res_id'] for row in records}))],
            groupby=['model', 'res_id'], aggregates=['__count'],
        )
        # The two `in` clauses are a cross product, so only the pairs actually
        # asked for count.
        return sum(count for model, res_id, count in groups
                   if (model, res_id) in wanted)

    def _correspondent(self, messages):
        """The customer on this thread, which is not whoever wrote last.

        Reply to a customer and the newest message is yours, so asking the
        newest message who it is from offers your own company as the contact.
        """
        for message in messages:
            if message.x_direction == 'incoming' and message.author_id:
                return message.author_id.commercial_partner_id
        return messages[:1].author_id.commercial_partner_id

    # ------------------------------------------------------------------
    # Row builders
    # ------------------------------------------------------------------

    def _conversation_row(self, newest, count):
        """One line in the list, built from the newest message of the group."""
        record_name = newest.x_document_name or newest.record_name or ''
        return {
            'model': newest.model or False,
            'res_id': newest.res_id or 0,
            'message_id': newest.id,
            'subject': newest.subject or _('(no subject)'),
            'preview': self._preview(newest.body),
            'correspondent': (newest.author_id.display_name
                              or newest.email_from or ''),
            'partner_id': newest.author_id.commercial_partner_id.id or False,
            'date': newest.date,
            'count': count,
            'record_name': record_name,
            # The newest message speaks for the conversation: a thread whose
            # last mail you have read is a thread you are up to date on, which
            # is what every mail client means by the dot.
            'unread': not newest.x_is_read,
            'mailbox': newest.x_mailbox_id.email or '',
            # Which mailbox this conversation arrived on, beside the address
            # the row draws. It is what the reply sends from: under All
            # mailboxes the screen has no mailbox of its own, and answering
            # from whichever address `_resolve_route()` picks is a mail the
            # customer never wrote to.
            'mailbox_id': newest.x_mailbox_id.id or False,
        }

    def _thread_row(self, message):
        """One mail under an unfolded conversation: who, when, one line.

        The same three things the conversation row above it carries, about one
        message instead of the newest. Everything else -- the body, the
        recipients, the attachments -- belongs to the conversation pane, which
        is what clicking this row opens.
        """
        return {
            'id': message.id,
            'author': message.author_id.display_name or message.email_from or '',
            'author_id': message.author_id.id or False,
            'date': message.date,
            'preview': self._preview(message.body),
            'unread': not message.x_is_read,
        }

    def _message_row(self, message):
        row = {
            'id': message.id,
            'kind': self._kind_of(message),
            'date': message.date,
            'subject': message.subject or '',
            'author': message.author_id.display_name or message.email_from or '',
            'author_id': message.author_id.id or False,
            # The address behind the name, for the header's From line. The
            # contact's own when there is one; otherwise what the wire said.
            'author_email': (message.author_id.email
                             or self._first_address(message.email_from)),
            'body': message.body or '',
            'direction': message.x_direction or '',
            'model': message.model or False,
            'res_id': message.res_id or 0,
            'record_name': message.x_document_name or message.record_name or '',
            'mailbox': message.x_mailbox_id.email or '',
            # The mail's own To/Cc when the sync or the send path wrote them,
            # and Odoo's notified partners for everything else (mail posted
            # from the chatter, and every message that predates the two
            # columns). An incoming mail with neither still arrived somewhere:
            # the mailbox it came in on is the one To that is always true.
            'recipients': message.x_email_to or ', '.join(
                p.email or p.display_name for p in message.partner_ids
            ) or (message.x_direction == 'incoming'
                  and message.x_mailbox_id.email or ''),
            'cc': message.x_email_cc or '',
        }
        if row['kind'] == 'event':
            # A record event usually has no body at all: what happened is in
            # the tracking values, and without them the row is a blank line.
            row['tracking'] = self._tracking_rows(message)
        return row

    @staticmethod
    def _first_address(value):
        """The bare address out of `Name <addr>`, or '' when there is none."""
        addresses = email_split(value or "")
        return addresses[0] if addresses else ''

    def _kind_of(self, message):
        """Mail, an internal note, or something the record did to itself.

        Direction decides first. A reply this module sent through the chatter
        is a `comment` that went out over the wire, and calling it a note
        would put correspondence behind the "internal" marker -- which is the
        one mistake on this screen a customer eventually reads about.
        """
        if message.x_direction or message.message_type == 'email':
            return 'mail'
        if message.message_type == 'notification':
            return 'event'
        return 'note'

    def _tracking_rows(self, message):
        """What changed, in three strings the client draws on one line.

        No `sudo()`: Odoo hides the tracking of a field the reader may not see
        by hiding the row, and that is the answer this screen wants too.
        """
        rows = []
        for value in message.tracking_value_ids:
            rows.append({
                'field': value.field_id.field_description or value.field_id.name or '',
                'old': self._tracking_text(value, 'old'),
                'new': self._tracking_text(value, 'new'),
            })
        return rows

    def _tracking_text(self, value, side):
        """One side of a tracked change, whichever typed column holds it."""
        for suffix in ('char', 'text', 'datetime', 'date',
                       'monetary', 'float', 'integer'):
            name = f'{side}_value_{suffix}'
            if name not in value._fields:
                continue
            raw = value[name]
            if raw is False or raw is None or raw == '':
                continue
            return str(raw)
        return ''

    def _files_for(self, model, res_id, messages):
        """Every file on this conversation, newest first.

        The same list the chatter draws, from the same two places: the
        attachments on the record itself -- which is what Odoo's own file box
        shows, uploads included -- and the attachments that arrived on the
        mail. A file somebody attaches here is a file on the record, so
        reading only the messages would have made an upload disappear on the
        next read.

        The rows are Odoo's own `ir.attachment` store format, because the tab
        is Odoo's own `AttachmentList`: the same cards, the same preview, the
        same download and delete. `ids` carries the order -- the store payload
        is keyed by model and says nothing about it.

        The `sudo()` is the one `mail.message` takes for its own
        `attachment_ids`: the messages came out of an access-checked search,
        and an attachment on a message somebody may read is one they may read.
        The record's own files are asked for with the reader's rights, which
        is what the chatter would have done.
        """
        attachments = self.env['ir.attachment'].sudo()
        if model and res_id:
            record = self.env[model].browse(res_id).exists()
            if record and record.has_access('read') and hasattr(
                    record, '_get_mail_thread_data_attachments'):
                attachments |= record._get_mail_thread_data_attachments().sudo()
        if model:
            source = self.env['mail.message'].search(
                [('model', '=', model), ('res_id', '=', res_id),
                 ('attachment_ids', '!=', False)],
                order='date desc, id desc', limit=50,
            )
        else:
            source = messages
        for message in source:
            attachments |= message.sudo().attachment_ids
        # One order for two sources, and it is the chatter's: newest first.
        # Capped, because a tab is not a document management system: a record
        # with a thousand files is one somebody opens the record for.
        attachments = attachments.sorted('id', reverse=True)[:FILE_PAGE]
        return {
            'ids': attachments.ids,
            'store': Store().add(attachments).get_result() if attachments else {},
        }

    def _activities_for(self, records):
        """What is still open on the records this conversation touched.

        The chip row has already been through `_filtered_access`, so this asks
        about records the reader may open and no others.
        """
        by_model = {}
        for row in records:
            by_model.setdefault(row['model'], []).append(row['res_id'])
        activities = self.env['mail.activity']
        for model, ids in by_model.items():
            activities |= activities.search(
                [('res_model', '=', model), ('res_id', 'in', ids)],
                order='date_deadline asc', limit=20,
            )
        return self._activity_rows(
            activities.sorted(lambda a: a.date_deadline or datetime.max.date())[:20])

    def _records_for(self, messages):
        """Every record this conversation touched, newest first.

        The messages come first and the index second. `pan.mail.thread.link` is
        not access controlled, so it may only ever widen a name, never a
        result: a record whose messages this user cannot read was never in
        `messages` to begin with. Reading it takes `sudo()` because the ACL on
        the index is mailbox-manager only, and every row it yields is then put
        back through `_filtered_access`, so the sudo buys the lookup and not
        the answer.

        Batched by model. The alternative is four queries per linked record,
        and a thread that crossed a quote, a ticket and an invoice is the
        normal case rather than the exotic one.
        """
        seen = {}
        for message in messages:
            if not message.model or not message.res_id:
                continue
            # `messages` is ordered newest first, so the first date wins.
            seen.setdefault((message.model, message.res_id), message.date)

        for model, res_id, date in self._linked_records(messages):
            seen.setdefault((model, res_id), date)

        by_model = {}
        for (model, res_id) in seen:
            by_model.setdefault(model, []).append(res_id)

        names, labels, icons = {}, {}, {}
        for model, ids in by_model.items():
            # A link outlives the module that wrote it: Helpdesk is Enterprise,
            # and a community database can hold rows naming a model it has not
            # got.
            if model not in self.env:
                continue
            records = self.env[model].browse(ids).exists()
            for record in records._filtered_access('read'):
                names[(model, record.id)] = record.display_name
            labels[model] = self.env['ir.model']._get(model).name or model
            icons[model] = self._model_icon(model)

        rows = sorted(seen.items(),
                      key=lambda item: item[1] or datetime.min, reverse=True)
        return [{
            'model': model,
            'res_id': res_id,
            'name': names[(model, res_id)],
            'model_label': labels.get(model, model),
            'icon': icons.get(model, False),
        } for (model, res_id), _date in rows if (model, res_id) in names]

    def _model_icon(self, model):
        """The icon of the app a record belongs to, as a URL, or False.

        The app is the one whose menu opens the model: the tile the reader
        knows from the app switcher. That is not always the module that
        defined the model. `res.partner` is base's, and base's own tile is a
        teal cube nobody recognises, while the Contacts app is what shows a
        contact; `sale.order` is sale's, and the Sales tile is
        sale_management's. When several apps open the model, the first in
        the switcher wins (Contacts before Sales for a contact). A model no
        menu opens falls back to the module that defined it, except base,
        whose cube is worse than no icon at all.
        """
        if model not in self.env:
            return False
        icon = self._app_icon_by_menu(model)
        if icon:
            return icon
        module = self.env[model]._original_module
        if not module or module == 'base':
            return False
        return '/%s/static/description/icon.png' % module

    def _app_icon_by_menu(self, model):
        """The `web_icon` of the first app whose menu opens `model`, or False.

        `sudo` because the question is which tile, not whether the reader
        may open it: the chip carries the name of a record they can already
        read. An archived root (Sales without sale_management) is skipped,
        which is what the search does on its own.
        """
        actions = self.env['ir.actions.act_window'].sudo().search(
            [('res_model', '=', model)])
        if not actions:
            return False
        Menu = self.env['ir.ui.menu'].sudo()
        menus = Menu.search([
            ('action', 'in', ['ir.actions.act_window,%d' % a.id for a in actions]),
        ])
        root_ids = {int(menu.parent_path.split('/')[0]) for menu in menus}
        roots = Menu.search([('id', 'in', list(root_ids)), ('web_icon', '!=', False)],
                            order='sequence, id', limit=1)
        module, _sep, path = (roots.web_icon or '').partition(',')
        if not module or not path:
            return False
        return '/%s/%s' % (module, path)

    def _linked_records(self, messages):
        """The other records this thread touched, from the thread index.

        Resolved from the records the messages sit on, not from
        `x_provider_thread_id`: that field is legacy and no longer written, so
        reading it would quietly return nothing for every mail synced since.

        Which keys may cross a mailbox is the whole question here, and the
        index already answers it. A provider handle is mailbox-local -- Graph
        mints a `conversationId` per mailbox -- so the same string in another
        mailbox is a different conversation, and matching it would pull a
        stranger's record into the chip row. The `references` root is the RFC
        Message-ID every participant carries, so it means the same thing
        everywhere. Provider keys stay inside their mailbox; reference keys do
        not have to, and that is what lets a thread sales@ and support@ both
        saw show up as one conversation on two records.
        """
        Link = self.env['pan.mail.thread.link'].sudo()
        here = [(m.model, m.res_id) for m in messages if m.model and m.res_id]
        if not here:
            return []
        own = Link.search([
            ('model', 'in', [model for model, _res_id in here]),
            ('res_id', 'in', [res_id for _model, res_id in here]),
        ], limit=50)
        own = own.filtered(lambda link: (link.model, link.res_id) in here)
        if not own:
            return []

        by_reference = own.filtered(lambda link: link.key_type == 'rfc')
        by_provider = own - by_reference
        clauses = []
        if by_reference:
            clauses.append(Domain([('key_type', '=', 'rfc'),
                                   ('thread_id', 'in', by_reference.mapped('thread_id'))]))
        if by_provider:
            clauses.append(Domain([('thread_id', 'in', by_provider.mapped('thread_id')),
                                   ('mailbox_id', 'in', by_provider.mailbox_id.ids)]))
        if not clauses:
            return []

        siblings = Link.search(Domain.OR(clauses), limit=50)
        return [(link.model, link.res_id, link.last_seen)
                for link in siblings if link.model and link.res_id]

    def _rejected_for(self, messages):
        """What the matcher turned down, for a conversation nobody filed.

        No target name: the log captured it with sudo at ingest, and the person
        reading this screen is not necessarily allowed to know that a record of
        that name exists.
        """
        if not messages:
            return []
        logs = self.env['pan.mail.routing.log'].sudo().search([
            ('mail_message_id', 'in', messages.ids),
        ], limit=5)
        return [{
            'id': log.id,
            'outcome': log.outcome,
            'rule': log.rule or '',
            'rule_label': ROUTING_RULES.get(log.rule, log.rule or _('No rule')),
            'reason': log.reason or '',
            'candidates': log.candidate_count,
        } for log in logs]

    def _suggestion_for(self, messages):
        """The one record the ladder nearly picked, if the reader may see it.

        One, not a list. A screen that offers five possibilities asks the
        reader to do the matching we failed to do; a screen that offers one
        asks them to confirm or ignore, which is a decision a person makes in
        a second.

        Filtered through `_filtered_access` for the same reason `_rejected_for`
        carries no target name: the suggestion was computed with sudo at
        ingest, and naming a record somebody cannot open would tell them it
        exists.
        """
        if not messages:
            return False
        logs = self.env['pan.mail.routing.log'].sudo().search([
            ('mail_message_id', 'in', messages.ids),
            ('suggested_model', '!=', False),
        ], limit=5)
        for log in logs:
            if log.suggested_model not in self.env or not log.suggested_res_id:
                continue
            record = self.env[log.suggested_model].browse(log.suggested_res_id).exists()
            if not record or not record._filtered_access('read'):
                continue
            return {
                'model': log.suggested_model,
                'res_id': log.suggested_res_id,
                'name': record.display_name,
                'reason': log.suggested_reason or '',
                'model_label': self.env['ir.model']._get(log.suggested_model).name
                or log.suggested_model,
            }
        return False

    @api.model
    def link_targets(self, search=None):
        """Step one of the picker: which kind of record this mail belongs to.

        What this database already links mail to comes first -- the mailboxes'
        own routing targets and the models the log has seen, plus the contact,
        which is where unmatched mail lands anyway. That list is the whole
        answer on a database with a history and almost empty on a fresh one,
        which is the day the picker is needed most, so the rest of the page is
        filled with the other models that carry a chatter. Twelve rows either
        way, because both steps have a search box and a list longer than the
        dialog is a list nobody reads to the end.

        A search widens to every model with a chatter, matched on its label,
        the already-linked ones still first: a search for "lead" should offer
        the model the log knows before the ones nobody has filed a mail on.
        """
        self._check_caller()
        known = self._known_link_models()
        if not search:
            return self._link_target_rows(known + self._other_mail_models())

        found = self.env['ir.model'].sudo().search([
            ('is_mail_thread', '=', True),
            ('transient', '=', False),
            ('name', 'ilike', search.strip()),
        ], limit=60).mapped('model')
        ordered = ([name for name in known if name in found]
                   + [name for name in found if name not in known])
        return self._link_target_rows(ordered)

    def _known_link_models(self):
        """The models this database already files mail on, best first."""
        names = ['res.partner']
        names += self.env['pan.mail.mailbox'].sudo().search(
            [('alias_id.alias_model_id', '!=', False)]
        ).mapped('alias_id.alias_model_id.model')
        # `_read_group` with one groupby and no aggregate yields one-tuples.
        names += [
            group[0]
            for group in self.env['pan.mail.routing.log'].sudo()._read_group(
                [('model', '!=', False)], groupby=['model'], limit=20)
            if group[0]
        ]
        return names

    def _other_mail_models(self):
        """The filler under what this database already links mail to.

        A chatter is not enough: a mail blacklist and a scheduled action have
        one, and offering them is how a picker turns into a directory. The
        rule is the same one step two runs on -- the model has to know whose
        record it is, so a `partner_id` at a contact -- which leaves quotes,
        leads, tasks and tickets and drops the plumbing. Alphabetical, because
        it is a list somebody scans and not a ranking we can honestly make.
        """
        names = self.env['ir.model'].sudo().search([
            ('is_mail_thread', '=', True),
            ('transient', '=', False),
        ], order='name', limit=MAX_LINK_TARGETS * 20).mapped('model')
        keep = []
        for name in names:
            if name not in self.env:
                continue
            field = self.env[name]._fields.get('partner_id')
            if field and field.type == 'many2one' and field.comodel_name == 'res.partner':
                keep.append(name)
        return keep

    def _link_target_rows(self, names):
        """Names to picker rows, dropping what this reader may not write.

        `write` is the right question: putting somebody's correspondence on a
        record is a change to that record, and a model the reader may only
        read is not a place they may put mail.
        """
        rows, seen = [], set()
        for name in names:
            if name in seen or name not in self.env:
                continue
            seen.add(name)
            Model = self.env[name]
            if not hasattr(Model, 'message_post') or not Model.has_access('write'):
                continue
            rows.append({
                'model': name,
                'label': self.env['ir.model']._get(name).name or name,
            })
            if len(rows) >= MAX_LINK_TARGETS:
                break
        return rows

    @api.model
    def link_candidates(self, model, search=None, partner_id=None,
                        limit=MAX_LINK_CANDIDATES):
        """Step two: which record of that kind.

        With a search, `name_search` -- the same lookup every many2one on this
        database uses, so a quote is found here the way people already find
        quotes everywhere else.

        Without one, the records that already belong to the correspondent.
        That is the whole of the smart half: mail from bart@vandermolen.test,
        on a quote, opens on Vandermolen's quotes rather than on an empty
        search box. Two ways in, and only two: a `partner_id` pointing at a
        contact, or an `email_from`. A model that relates to a contact through
        anything else -- a `partner_ids`, a field of its own -- gets the most
        recent records and the search box. Guessing at a third relation would
        be a rule nobody could predict from the screen.
        """
        self._check_caller()
        Model = self._link_model(model)
        try:
            limit = int(limit or MAX_LINK_CANDIDATES)
        except (TypeError, ValueError):
            limit = MAX_LINK_CANDIDATES
        limit = max(min(limit, MAX_LINK_CANDIDATES), 1)

        if search and search.strip():
            found = Model.name_search(search.strip(), limit=limit)
            return {
                'related': False,
                'partner': '',
                'rows': [{'id': row[0], 'name': row[1]} for row in found],
            }

        partner = self.env['res.partner']
        if partner_id:
            partner = partner.browse(int(partner_id)).exists()
        domain = self._candidate_domain(Model, partner)
        records = Model.search(domain or [], limit=limit, order='id desc')
        # The company, not the person who wrote: it is whose records these are,
        # and a list of the company's quotes under one employee's name reads
        # as a mistake.
        family = partner.commercial_partner_id or partner
        return {
            'related': domain is not None,
            'partner': family.display_name if domain is not None else '',
            'rows': [{'id': record.id, 'name': record.display_name}
                     for record in records],
        }

    @api.model
    def new_mail_recipients(self, model, res_id):
        """Who a new mail on this record goes to: the record's own contact.

        The composer fills "To" from nothing on its own -- the chatter asks the
        record for its suggested recipients and hands them over, and the pane
        has to do the same or a new mail opens addressed to nobody. The answer
        is Odoo's own: `_message_get_default_recipients`, the rule every mail
        template sends by. A contact is its own recipient, a quote or a ticket
        gives its `partner_id`, a lead with only an `email_from` gives that
        address, and that address becomes a contact the way it does when the
        chatter's suggestion is accepted, through the same find-or-create.
        Followers are not asked: `message_post` reaches them on its own, and
        a "To" that repeats the follower list is the note-versus-mail
        confusion the composer exists to avoid.
        """
        self._check_caller()
        try:
            res_id = int(res_id)
        except (TypeError, ValueError):
            return []
        record = self._link_model(model).browse(res_id).exists()
        if not record:
            return []
        record.check_access('read')
        defaults = record._message_get_default_recipients()[record.id]
        if defaults['partner_ids']:
            return defaults['partner_ids']
        if defaults['email_to']:
            return record._partner_find_from_emails_single(
                defaults['email_to'].split(',')).ids
        return []

    def _link_model(self, model):
        """The model step two may read, or an error.

        `model` arrives over RPC and nothing stops a caller skipping step one,
        so the question step one answers is asked again here: mail goes where
        a chatter can carry it and the reader may write.
        """
        if not model or model not in self.env:
            raise AccessError(_("Mail cannot be linked to %s.", model))
        Model = self.env[model]
        if not hasattr(Model, 'message_post') or not Model.has_access('write'):
            raise AccessError(_("Mail cannot be linked to %s.", model))
        return Model

    def _candidate_domain(self, Model, partner):
        """What this correspondent already has on that model, or nothing.

        `child_of` the commercial partner rather than the partner itself: mail
        from one employee is about the company's quotes, and a contact person
        rarely carries the records.
        """
        if not partner:
            return None
        family = partner.commercial_partner_id or partner
        if Model._name == 'res.partner':
            return [('id', 'child_of', family.id)]
        field = Model._fields.get('partner_id')
        if field and field.type == 'many2one' and field.comodel_name == 'res.partner':
            return [('partner_id', 'child_of', family.id)]
        if 'email_from' in Model._fields and partner.email:
            return [('email_from', 'ilike', partner.email)]
        return None

    def _next_for(self, company):
        """This customer's open activities, soonest first.

        Scoped to the customer whose timeline is on screen. An earlier version
        OR'd in every activity assigned to the reader anywhere in the database,
        which put somebody else's stock count under a customer's
        correspondence.
        """
        return self._activity_rows(self.env['mail.activity'].search(
            [('res_model', '=', 'res.partner'), ('res_id', '=', company.id)],
            order='date_deadline asc', limit=5,
        ))

    def _activity_rows(self, activities):
        """One shape for an open activity, wherever it is listed."""
        return [{
            'id': activity.id,
            'summary': activity.summary or activity.activity_type_id.name or '',
            'type': activity.activity_type_id.name or '',
            'deadline': activity.date_deadline,
            'state': activity.state,
            'user': activity.user_id.display_name or '',
            'model': activity.res_model,
            'res_id': activity.res_id,
            'record_name': activity.res_name or '',
            'mine': activity.user_id == self.env.user,
        } for activity in activities]

    def _preview(self, body):
        """The snippet line: a body as text, cut to one line.

        Takes the html rather than the message, because a draft has a body and
        no message: the Drafts folder previews what was typed the way the
        other folders preview what arrived.

        The slice comes first. A real mail carries a signature, an inline
        stylesheet and the whole quoted history, and stripping all of that
        character by character to produce 140 characters is work nobody reads.
        `html2plaintext` also drops what is *inside* a `<style>` block, which
        hand-rolled tag stripping leaves behind as a line of CSS.
        """
        body = str(body or '')[:PREVIEW_SOURCE]
        # A short answer on top of a long quote previews as the answer and
        # then the quote's first line, which reads as if the customer wrote
        # both. Cut at the first quote marker; the conversation pane folds the same
        # markers (QUOTE_MARKERS in conversation_view.js), so the line here
        # and the open message agree on where "what they wrote" ends.
        quote = QUOTE_START.search(body)
        if quote:
            body = body[:quote.start()]
        return ' '.join(html2plaintext(body).split())[:140]
