# -*- coding: utf-8 -*-
"""
The read side of the conversation view.

This model stores nothing. It is a namespace for the queries the inbox screen
makes, and every one of them is a different `WHERE` over rows that already
exist: `mail.message` for the mail, `mail.activity` for what is next, and the
record's own tracked-field messages for the events. The design is in
`docs/plans/conversation-view.md`; how it is built, in
`docs/plans/conversation-view-build.md`.

Two rules hold this layer together.

**Start from the messages, never from the index.** Every query begins with a
search on `mail.message`, so the ORM applies the record rules before we group
anything. `pan.mail.thread.link` is not access controlled: reading it first
would let a thread key betray the existence of a record the user cannot open.
There is no `sudo()` here, on purpose, and a message on a record somebody
cannot read is absent from their result rather than hidden inside it.

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
import html
import logging

from odoo import models, api, _

_logger = logging.getLogger(__name__)

# One page. Deliberately small: the list is read, not scrolled through.
DEFAULT_LIMIT = 30

# The folders in the rail, in the order they are shown. The value is what the
# client sends back; the label is what a person reads.
FOLDERS = [
    ('inbox', 'Inbox'),
    ('needs_reply', 'Needs reply'),
    ('waiting', 'Waiting on customer'),
    ('sent', 'Sent'),
    ('unfiled_contact', 'On a contact only'),
    ('unfiled_none', 'Linked to nothing'),
]


class PanMailConversation(models.AbstractModel):
    """Queries behind the conversation view. No table, no stored fact."""

    _name = 'pan.mail.conversation'
    _description = 'Conversation View'

    # ------------------------------------------------------------------
    # The domain every folder is built from
    # ------------------------------------------------------------------

    def _base_domain(self, mailbox_id=None, partner_id=None, search=None):
        """Emails this user may read, optionally narrowed to one mailbox.

        `message_type = 'email'` is what keeps internal notes out of a screen
        that is about correspondence. Notes are still visible where they
        belong, in the chatter one pane to the right.
        """
        domain = [('message_type', '=', 'email')]
        if mailbox_id:
            domain.append(('x_mailbox_id', '=', mailbox_id))
        if partner_id:
            partner = self.env['res.partner'].browse(partner_id)
            # The company, not the person: jan@acme and inkoop@acme are one
            # correspondence, and `commercial_partner_id` is how Odoo says so.
            company = partner.commercial_partner_id or partner
            members = self.env['res.partner'].search(
                [('commercial_partner_id', '=', company.id)]
            )
            domain.append('|')
            domain.append(('author_id', 'in', members.ids))
            domain.append(('partner_ids', 'in', members.ids))
        if search:
            domain += ['|', ('subject', 'ilike', search),
                       ('email_from', 'ilike', search)]
        return domain

    def _folder_domain(self, folder):
        """The extra clauses one folder adds. Unknown folder means no clause."""
        if folder == 'needs_reply':
            return [('x_direction', '=', 'incoming')]
        if folder == 'waiting':
            return [('x_direction', '=', 'outgoing')]
        if folder == 'sent':
            return [('x_direction', '=', 'outgoing')]
        if folder == 'unfiled_contact':
            return [('model', '=', 'res.partner')]
        if folder == 'unfiled_none':
            return [('model', '=', False)]
        return []

    # ------------------------------------------------------------------
    # Public read methods, the whole API the client has
    # ------------------------------------------------------------------

    @api.model
    def folder_counts(self, mailbox_id=None):
        """How many conversations sit in each folder, for the rail.

        These are counted on every read. A stored counter would be one more
        fact that can disagree with the messages, and the whole point of this
        screen is that it holds none of those.
        """
        counts = []
        for value, label in FOLDERS:
            domain = self._base_domain(mailbox_id) + self._folder_domain(value)
            groups = self.env['mail.message'].read_group(
                domain, ['id:count'], ['model', 'res_id'], lazy=False,
            )
            counts.append({
                'id': value,
                'name': label,
                'count': len(groups),
            })
        return counts

    @api.model
    def search_conversations(self, mailbox_id=None, folder='inbox',
                             partner_id=None, search=None,
                             limit=DEFAULT_LIMIT, offset=0):
        """One page of conversations, newest first.

        Three queries: a read_group for the grouping, one read for the newest
        message of each group, one search for what is unread. Everything else
        on the row is derived from what those returned.
        """
        Message = self.env['mail.message']
        domain = (self._base_domain(mailbox_id, partner_id, search)
                  + self._folder_domain(folder))

        groups = Message.read_group(
            domain,
            ['id:count', 'date:max'],
            ['model', 'res_id'],
            lazy=False,
            orderby='date:max desc',
            limit=limit,
            offset=offset,
        )
        if not groups:
            return []

        rows = []
        for group in groups:
            newest = Message.search(
                domain + [('model', '=', group['model']),
                          ('res_id', '=', group['res_id'])],
                order='date desc, id desc', limit=1,
            )
            if not newest:
                continue
            rows.append(self._conversation_row(newest, group['__count']))
        return rows

    @api.model
    def read_conversation(self, model, res_id, mailbox_id=None,
                          limit=50, offset=0):
        """The messages of one conversation, oldest last, with its records.

        `records` is the chip row: every record this thread touched, newest
        first. `rejected` is what the matcher considered and turned down, which
        is only interesting when nothing was filed.
        """
        Message = self.env['mail.message']
        domain = self._base_domain(mailbox_id) + [
            ('model', '=', model or False),
            ('res_id', '=', res_id or 0),
        ]
        # Newest N, shown oldest first: the page you want is the end of the
        # thread, and the order you read it in is downwards.
        messages = Message.search(
            domain, order='date desc, id desc', limit=limit, offset=offset,
        )
        return {
            'messages': [self._message_row(m)
                         for m in messages.sorted(lambda m: (m.date, m.id))],
            'records': self._records_for(messages),
            'rejected': self._rejected_for(messages),
        }

    @api.model
    def record_conversations(self, model, res_id):
        """What the chatter's door needs: is there mail, and is it all here.

        `elsewhere` is the reason the extra line exists. When it is zero the
        client draws nothing, because a line that says "everything is already
        on this page" is noise.
        """
        Message = self.env['mail.message']
        here = Message.search([
            ('message_type', '=', 'email'),
            ('model', '=', model),
            ('res_id', '=', res_id),
        ])
        if not here:
            return {'conversations': 0, 'here': 0, 'elsewhere': 0,
                    'partner_id': False, 'partner_name': ''}

        records = self._records_for(here)
        elsewhere = 0
        for record in records:
            if record['model'] == model and record['res_id'] == res_id:
                continue
            elsewhere += Message.search_count([
                ('message_type', '=', 'email'),
                ('model', '=', record['model']),
                ('res_id', '=', record['res_id']),
            ])

        partner = here[0].author_id.commercial_partner_id
        return {
            'conversations': len(records) or 1,
            'here': len(here),
            'elsewhere': elsewhere,
            'partner_id': partner.id or False,
            'partner_name': partner.display_name or '',
        }

    @api.model
    def customer_timeline(self, partner_id, kinds=None, limit=40, offset=0):
        """One axis for a customer: what is next, then what happened.

        `next` is Salesforce's future half and Odoo's `mail.activity`
        unchanged. `items` is the past, merged from the kinds asked for.
        """
        kinds = kinds or ['mail', 'activity', 'event']
        partner = self.env['res.partner'].browse(partner_id)
        company = partner.commercial_partner_id or partner

        items = []
        if 'mail' in kinds:
            for message in self.env['mail.message'].search(
                self._base_domain(partner_id=partner_id),
                order='date desc, id desc', limit=limit, offset=offset,
            ):
                row = self._message_row(message)
                row['kind'] = 'mail'
                items.append(row)

        if 'event' in kinds:
            for message in self.env['mail.message'].search([
                ('message_type', '=', 'notification'),
                ('model', '=', 'res.partner'),
                ('res_id', '=', company.id),
            ], order='date desc, id desc', limit=limit):
                items.append({
                    'kind': 'event',
                    'id': message.id,
                    'date': message.date,
                    'subject': message.subject or _('Update'),
                    'author': message.author_id.display_name or '',
                    'model': message.model,
                    'res_id': message.res_id,
                })

        items.sort(key=lambda row: (row.get('date') or '', row['id']),
                   reverse=True)
        return {
            'next': self._next_for(company) if 'activity' in kinds else [],
            'items': items[:limit],
        }

    # ------------------------------------------------------------------
    # Row builders
    # ------------------------------------------------------------------

    def _conversation_row(self, newest, count):
        """One line in the list, built from the newest message of the group."""
        record_name = newest.x_document_name or newest.record_name or ''
        return {
            'model': newest.model or False,
            'res_id': newest.res_id or 0,
            'subject': newest.subject or _('(no subject)'),
            'preview': self._preview(newest),
            'correspondent': (newest.author_id.display_name
                              or newest.email_from or ''),
            'partner_id': newest.author_id.commercial_partner_id.id or False,
            'date': newest.date,
            'count': count,
            'record_name': record_name,
            'unread': self._unread_for(newest),
            # The last word was theirs. Worked out here every time, so it
            # is never a day out of date.
            'waiting_on_us': newest.x_direction == 'incoming',
            'mailbox': newest.x_mailbox_id.email or '',
        }

    def _message_row(self, message):
        return {
            'id': message.id,
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
            'recipients': message.partner_ids.mapped('display_name'),
        }

    def _records_for(self, messages):
        """Every record this conversation touched, newest first.

        Read from the thread index, but only *after* the messages came back:
        the index is not access controlled, so it may widen a name, never a
        result. A record whose messages this user cannot read contributes
        nothing here because it was never in `messages` to begin with.
        """
        seen = {}
        for message in messages:
            if not message.model or not message.res_id:
                continue
            seen[(message.model, message.res_id)] = message.date

        thread_ids = [t for t in messages.mapped('x_provider_thread_id') if t]
        if thread_ids:
            links = self.env['pan.mail.thread.link'].search([
                ('thread_id', 'in', thread_ids),
            ])
            for link in links:
                key = (link.model, link.res_id)
                if key in seen or not link.model:
                    continue
                # Only if this user can actually open it.
                record = self.env[link.model].browse(link.res_id)
                if record.exists() and record.has_access('read'):
                    seen[key] = link.last_seen

        rows = []
        for (model, res_id), date in sorted(
            seen.items(), key=lambda item: item[1] or '', reverse=True
        ):
            record = self.env[model].browse(res_id)
            rows.append({
                'model': model,
                'res_id': res_id,
                'name': record.display_name if record.exists() else '',
                'model_label': self.env['ir.model']._get(model).name or model,
            })
        return rows

    def _rejected_for(self, messages):
        """What the matcher turned down, for a conversation nobody filed."""
        if not messages:
            return []
        logs = self.env['pan.mail.routing.log'].search([
            ('mail_message_id', 'in', messages.ids),
        ], limit=5)
        return [{
            'id': log.id,
            'outcome': log.outcome,
            'rule': log.rule or '',
            'reason': log.reason or '',
            'target': log.target_name or '',
            'candidates': log.candidate_count,
        } for log in logs]

    def _next_for(self, company):
        """Open activities on this customer's records, soonest first."""
        activities = self.env['mail.activity'].search([
            '|',
            '&', ('res_model', '=', 'res.partner'), ('res_id', '=', company.id),
            ('user_id', '=', self.env.user.id),
        ], order='date_deadline asc', limit=5)
        return [{
            'id': activity.id,
            'summary': activity.summary or activity.activity_type_id.name or '',
            'deadline': activity.date_deadline,
            'state': activity.state,
            'user': activity.user_id.display_name or '',
            'model': activity.res_model,
            'res_id': activity.res_id,
            'record_name': activity.res_name or '',
        } for activity in activities]

    def _unread_for(self, message):
        """Odoo's own needaction row, never a flag of ours."""
        return bool(self.env['mail.notification'].search_count([
            ('mail_message_id', '=', message.id),
            ('res_partner_id', '=', self.env.user.partner_id.id),
            ('notification_type', '=', 'inbox'),
            ('is_read', '=', False),
        ]))

    def _preview(self, message):
        """The snippet line: the body without its markup, cut to one line."""
        text = str(message.body or '')
        for tag in ('<br>', '<br/>', '</p>', '</div>'):
            text = text.replace(tag, ' ')
        while '<' in text and '>' in text[text.index('<'):]:
            start = text.index('<')
            end = text.index('>', start)
            text = text[:start] + text[end + 1:]
        text = html.unescape(text)
        return ' '.join(text.split())[:140]
