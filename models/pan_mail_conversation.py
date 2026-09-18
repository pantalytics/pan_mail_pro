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
exactly as the chatter does.

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
from odoo.tools import html2plaintext

_logger = logging.getLogger(__name__)

# What the Files tab draws at most. A tab, not a document archive: past this
# many, the record's own Files box is the screen for it.
FILE_PAGE = 50

# One page. Deliberately small: the list is read, not scrolled through.
DEFAULT_LIMIT = 30

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

# How much of a body the one-line preview looks at. A real mail carries a
# signature, an inline stylesheet and the whole quoted history; the preview is
# 140 characters.
PREVIEW_SOURCE = 8000
# Where the quoted history starts, as the mail clients people write to us
# from mark it. The same list the thread pane folds, so the snippet and the
# open message end "what they wrote" at the same place.
QUOTE_START = re.compile(
    r'<blockquote\b|class="[^"]*\b(?:gmail_quote|moz-cite-prefix|OutlookMessageHeader)\b'
    r'|data-o-mail-quote|id="(?:divRplyFwdMsg|appendonsend)"',
    re.IGNORECASE)

# The rail, in the order it is drawn. A mailbox and the two folders every
# mail client has, because the rail is the part of this screen people already
# know how to read. Our own states are not folders and do not belong here;
# they filter the list, one pane to the right.
RAIL_FOLDERS = [
    ('inbox', 'Inbox'),
    ('sent', 'Sent'),
]

# The states worth filtering a folder down to. These are ours, not the
# provider's, so they read as filters over a list rather than as folders
# holding mail of their own -- the difference between a view and a place.
#
# They are all about *linking*, never about the mail itself. A mailbox has
# read and unread, and that is the whole vocabulary of a mail list; a state
# like "needs reply" is one we made up, and it was ours to keep correct on a
# screen that reads mail somebody already triages in Outlook. Read status is
# Odoo's, and it is enough.
LIST_FILTERS = [
    ('unlinked_contact', 'On a contact only'),
    ('unlinked_none', 'Linked to nothing'),
]

KINDS = ({value: 'folder' for value, _label in RAIL_FOLDERS}
         | {value: 'filter' for value, _label in LIST_FILTERS})

# The matcher's rule names, in words. The screen shows why a mail was not
# filed, and `subject_participants` is not why anything happened.
ROUTING_RULES = {
    'odoo_headers': 'Our own headers on a reply',
    'references': 'The reply headers of the thread',
    'thread_link': 'A thread already filed on this record',
    'thread_link_legacy': 'An older thread id for this record',
    'subject_participants': 'The same subject and the same people',
}


class PanMailConversation(models.AbstractModel):
    """Queries behind the conversation view. No table, no stored fact."""

    _name = 'pan.mail.conversation'
    _description = 'Conversation View'

    # ------------------------------------------------------------------
    # The domain every folder is built from
    # ------------------------------------------------------------------

    def _base_domain(self, mailbox_id=None, partner_id=None, search=None):
        """Emails this user may read, optionally narrowed to one mailbox.

        `message_type = 'email'` is what keeps internal notes out of the list
        and out of the Mail tab. They are one tab away, in Everything, which
        is where the chatter's history went when the record pane lost it.
        """
        domain = [('message_type', '=', 'email')]
        if mailbox_id:
            domain.append(('x_mailbox_id', '=', mailbox_id))
        if partner_id:
            partner = self.env['res.partner'].browse(partner_id)
            # The company, not the person: jan@acme and inkoop@acme are one
            # correspondence, and `commercial_partner_id` is how Odoo says so.
            # Written as a dotted path so the ORM emits a subquery. Fetching
            # the sibling contacts first pastes a few thousand ids into every
            # clause of every query a large customer's timeline makes.
            company = partner.commercial_partner_id or partner
            domain += ['|',
                       ('author_id.commercial_partner_id', '=', company.id),
                       ('partner_ids.commercial_partner_id', '=', company.id)]
        if search:
            domain += ['|', ('subject', 'ilike', search),
                       ('email_from', 'ilike', search)]
        return domain

    def _folder_domain(self, folder):
        """The extra clauses the rail folder adds to the grouping query.

        Sent is "this conversation was written in", not "we spoke last": a
        thread the customer answered is still one you sent in, which is what
        the word means in the mail client open next to this one.
        """
        if folder == 'sent':
            return [('x_direction', '=', 'outgoing')]
        return []

    def _filter_domain(self, filter_name):
        """The extra clauses a list filter adds to the grouping query.

        Every filter here is a clause on the message, so the grouping query
        is the whole answer.
        """
        if filter_name == 'unlinked_contact':
            return [('model', '=', 'res.partner')]
        if filter_name == 'unlinked_none':
            return [('model', '=', False)]
        return []

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
    def folder_counts(self, mailbox_id=None, folder=None,
                      partner_id=None, search=None):
        """The numbers on the rail, and on the filters of one folder.

        Counted on every read, capped at `COUNT_CAP`. A stored counter would be
        one more fact that can disagree with the messages; an exact count means
        aggregating every row the reader can see, once per entry, on every
        click. The cap costs a "+" on the label and saves the scan.

        It takes the same `partner_id` and `search` the list takes, so the rail
        and the list always describe the same mail.

        `folder` is the one the reader has open. The filters are counted
        inside it and only for that mailbox, because they are a filter row
        over one list rather than a second rail: a mailbox standing open in
        the rail costs its two folders, not four.
        """
        self._check_caller()
        base = self._base_domain(mailbox_id, partner_id, search)
        folders = [self._count_entry(base, value, label,
                                     self._folder_domain(value))
                   for value, label in RAIL_FOLDERS]
        filters = []
        if folder:
            within = base + self._folder_domain(folder)
            filters = [
                self._count_entry(within, value, label,
                                  self._filter_domain(value))
                for value, label in LIST_FILTERS
            ]
        return {'folders': folders, 'filters': filters}

    def _count_entry(self, domain, value, label, extra):
        """One number for the rail or the filter row, capped."""
        groups = self.env['mail.message']._read_group(
            domain + extra, groupby=['model', 'res_id'],
            aggregates=['__count', 'date:max'],
            order='date:max DESC, model ASC, res_id ASC',
            limit=COUNT_CAP + 1,
        )
        if value == 'unlinked_none':
            # Unfiled mail does not group: every row is its own conversation,
            # and grouping on (model, res_id) counts the whole pile as one.
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

    @api.model
    def search_conversations(self, mailbox_id=None, folder='inbox',
                             filter_name=None, partner_id=None, search=None,
                             limit=DEFAULT_LIMIT, offset=0):
        """One page of conversations, newest first.

        Two dimensions: the folder from the rail, and the filter over it. A
        folder is a place mail is, a filter is a question about it, and the
        screen keeps them apart because the rail is the part people already
        know how to read.

        A fixed number of queries, whatever the page size: the grouping, the
        newest message of each group, the message counts, the unread rows, and
        the display names of the records. The newest messages come back as one
        recordset on purpose, so the ORM prefetches their authors, mailboxes
        and document names for the whole page instead of once per row.
        """
        self._check_caller()
        limit, offset = self._page(limit, offset)
        base = self._base_domain(mailbox_id, partner_id, search)
        narrowed = base + self._folder_domain(folder)

        # Mail nobody filed is not one conversation. Grouping it on
        # (model, res_id) would collapse every unmatched message in the
        # database into a single row belonging to nobody, which is the exact
        # opposite of the state this filter exists to make reviewable.
        if filter_name == 'unlinked_none':
            return self._unlinked_rows(narrowed, limit, offset)

        Message = self.env['mail.message']
        domain = narrowed + self._filter_domain(filter_name)

        groups = Message._read_group(
            domain,
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
        unread = self._unread_ids(newest)
        return [
            self._conversation_row(
                message, counts.get((message.model, message.res_id), 1), unread)
            for message in newest
        ]

    @api.model
    def read_conversation(self, model, res_id, mailbox_id=None,
                          message_id=None, limit=50, offset=0, scope='mail'):
        """One conversation, and everything the four tabs over it draw.

        `messages` is the thread, oldest last. `records` is the chip row:
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
        base = self._base_domain(mailbox_id)
        if model:
            target = [('model', '=', model), ('res_id', '=', res_id)]
            domain = Domain(base + target)
            if scope == 'all':
                # A note and a stage change belong to the record, not to a
                # mailbox, and carry no direction. Running them through the
                # correspondence clauses would empty the tab that exists to
                # show them.
                domain = Domain.OR([domain, Domain(
                    target + [('message_type', 'in', ('comment', 'notification'))])])
        elif message_id:
            domain = Domain(base + [('id', '=', int(message_id))])
        else:
            # `= 0` does not match a NULL res_id, so a conversation linked to
            # nothing, asked for by key rather than by message, has to say False.
            domain = Domain(base + [('model', '=', False), ('res_id', '=', False)])

        # Newest N, shown oldest first: the page you want is the end of the
        # thread, and the order you read it in is downwards.
        messages = Message.search(
            domain, order='date desc, id desc', limit=limit, offset=offset,
        )
        records = self._records_for(messages)
        return {
            'messages': [self._message_row(m)
                         for m in messages.sorted(lambda m: (m.date, m.id))],
            'records': records,
            'rejected': [] if model else self._rejected_for(messages),
            'suggestion': self._suggestion_for(messages),
            'files': self._files_for(model, res_id, messages),
            'activities': self._activities_for(records),
        }

    @api.model
    def record_conversations(self, model, res_id):
        """What the chatter's door needs: is there mail, and is it all here.

        `elsewhere` is the reason the extra line exists. When it is zero the
        client draws nothing, because a line that says "everything is already
        on this page" is noise.
        """
        self._check_caller()
        Message = self.env['mail.message']
        base = [
            ('message_type', '=', 'email'),
            ('model', '=', model),
            ('res_id', '=', res_id),
        ]
        here_count = Message.search_count(base)
        if not here_count:
            return {'conversations': 0, 'here': 0, 'elsewhere': 0,
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
            'here': here_count,
            'elsewhere': elsewhere,
            'partner_id': self._correspondent(here).id or False,
            'partner_name': self._correspondent(here).display_name or '',
        }

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
    # Batch helpers: one query for the page, never one per row
    # ------------------------------------------------------------------

    def _unlinked_rows(self, domain, limit, offset):
        """One row per unlinked message, because that is what it is.

        Nothing groups these: they are the mails the matcher could not place,
        and the whole point of the filter is to look at them one at a time.
        """
        messages = self.env['mail.message'].search(
            domain + [('model', '=', False)],
            order='date desc, id desc', limit=limit, offset=offset,
        )
        unread = self._unread_ids(messages)
        rows = []
        for message in messages:
            row = self._conversation_row(message, 1, unread)
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

    def _unread_ids(self, messages):
        """Which of these messages are unread, in one query.

        Odoo's own needaction row, never a flag of ours.
        """
        if not messages:
            return set()
        notifications = self.env['mail.notification'].search([
            ('mail_message_id', 'in', messages.ids),
            ('res_partner_id', '=', self.env.user.partner_id.id),
            ('notification_type', '=', 'inbox'),
            ('is_read', '=', False),
        ])
        return set(notifications.mail_message_id.ids)

    def _count_on_records(self, records):
        """How many emails sit on these records, in one grouped query."""
        wanted = {(row['model'], row['res_id']) for row in records}
        groups = self.env['mail.message']._read_group(
            [('message_type', '=', 'email'),
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

    def _conversation_row(self, newest, count, unread_ids):
        """One line in the list, built from the newest message of the group."""
        record_name = newest.x_document_name or newest.record_name or ''
        return {
            'model': newest.model or False,
            'res_id': newest.res_id or 0,
            'message_id': newest.id,
            'subject': newest.subject or _('(no subject)'),
            'preview': self._preview(newest),
            'correspondent': (newest.author_id.display_name
                              or newest.email_from or ''),
            'partner_id': newest.author_id.commercial_partner_id.id or False,
            'date': newest.date,
            'count': count,
            'record_name': record_name,
            'unread': newest.id in unread_ids,
            'mailbox': newest.x_mailbox_id.email or '',
        }

    def _message_row(self, message):
        row = {
            'id': message.id,
            'kind': self._kind_of(message),
            'date': message.date,
            'subject': message.subject or '',
            'author': message.author_id.display_name or message.email_from or '',
            'author_id': message.author_id.id or False,
            'body': message.body or '',
            'direction': message.x_direction or '',
            'model': message.model or False,
            'res_id': message.res_id or 0,
            'record_name': message.x_document_name or message.record_name or '',
            'mailbox': message.x_mailbox_id.email or '',
            # The mail's own To/Cc when the sync wrote them, and Odoo's
            # notified partners for everything else (mail sent from the
            # chatter, and every message that predates the two columns).
            'recipients': message.x_email_to or ', '.join(
                p.email or p.display_name for p in message.partner_ids),
            'cc': message.x_email_cc or '',
        }
        if row['kind'] == 'event':
            # A record event usually has no body at all: what happened is in
            # the tracking values, and without them the row is a blank line.
            row['tracking'] = self._tracking_rows(message)
        return row

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

        names, labels = {}, {}
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

        rows = sorted(seen.items(),
                      key=lambda item: item[1] or datetime.min, reverse=True)
        return [{
            'model': model,
            'res_id': res_id,
            'name': names[(model, res_id)],
            'model_label': labels.get(model, model),
        } for (model, res_id), _date in rows if (model, res_id) in names]

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

    def _preview(self, message):
        """The snippet line: the body as text, cut to one line.

        The slice comes first. A real mail carries a signature, an inline
        stylesheet and the whole quoted history, and stripping all of that
        character by character to produce 140 characters is work nobody reads.
        `html2plaintext` also drops what is *inside* a `<style>` block, which
        hand-rolled tag stripping leaves behind as a line of CSS.
        """
        body = str(message.body or '')[:PREVIEW_SOURCE]
        # A short answer on top of a long quote previews as the answer and
        # then the quote's first line, which reads as if the customer wrote
        # both. Cut at the first quote marker; the thread pane folds the same
        # markers (QUOTE_MARKERS in conversation_view.js), so the line here
        # and the open message agree on where "what they wrote" ends.
        quote = QUOTE_START.search(body)
        if quote:
            body = body[:quote.start()]
        return ' '.join(html2plaintext(body).split())[:140]
