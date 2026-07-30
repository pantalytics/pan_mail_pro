# -*- coding: utf-8 -*-
"""
Extension of mail.message: provider threading keys, plus the fields the
communication lens is built on.

Two separate concerns share this file because they share a table:

1. Threading. Microsoft Graph does not always return In-Reply-To headers,
   especially for Sent Items in shared mailboxes, so conversationId is stored
   as a fallback.

2. The lens. Odoo links a message to its document through `model` (a char) and
   `res_id` (an int) — a pointer with no foreign key, which cannot be grouped
   or clicked through. `mail.message` is also one table for notes, emails,
   system logs and chats, distinguished only by `message_type`. The result is
   that "where did this email end up?" has no answer in standard Odoo. These
   fields give it one, without introducing a second copy of the mail.

Everything here is written on the largest table in a production database, so
each field below justifies its storage. Partial indexes keep the index off the
rows that are notes and system logs, which are the overwhelming majority.
"""
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class MailMessage(models.Model):
    """Extend mail.message with provider tracking and the communication lens."""
    _inherit = 'mail.message'

    # -------------------------------------------------------------------------
    # Provider threading keys
    # -------------------------------------------------------------------------
    x_microsoft_message_id = fields.Char(
        string='Microsoft Message ID',
        help='Microsoft Graph internetMessageId - used for In-Reply-To threading',
        index='btree_not_null',
    )

    x_microsoft_conversation_id = fields.Char(
        string='Microsoft Conversation ID',
        help='Microsoft Graph conversationId for email threading',
        index='btree_not_null',
    )

    # -------------------------------------------------------------------------
    # Communication lens
    # -------------------------------------------------------------------------
    x_direction = fields.Selection(
        [('incoming', 'Incoming'), ('outgoing', 'Outgoing')],
        string='Direction',
        index='btree_not_null',
        help='Set when this message was sent or received through Mail Pro. '
             'Empty for notes, system logs and mail that predates the feature.',
    )

    x_mailbox_id = fields.Many2one(
        'x_microsoft.mailbox',
        string='Mailbox',
        ondelete='set null',
        index='btree_not_null',
        help='The mailbox this message was sent from or received in.',
    )

    x_account_id = fields.Many2one(
        'pan.mail.account',
        string='Account',
        ondelete='set null',
        # Deliberately unindexed: it is shown, never grouped or searched by.
        help='The credentials that carried this message.',
    )

    x_res_model_id = fields.Many2one(
        'ir.model',
        string='Linked Document Model',
        compute='_compute_x_res_model_id',
        store=True,
        index='btree_not_null',
        ondelete='cascade',
        help='Model of the linked document, as a relation rather than a string, '
             'so the lens can group and filter on it.',
    )

    x_document_name = fields.Char(
        string='Linked To',
        compute='_compute_x_document_name',
        help='Display name of the linked document.',
    )

    x_delivery_state = fields.Selection(
        [('pending', 'Pending'), ('sent', 'Delivered'), ('failed', 'Failed')],
        string='Delivery',
        compute='_compute_x_delivery_state',
        search='_search_x_delivery_state',
        compute_sudo=True,
        help='Aggregated delivery status of the notifications for this message.',
    )

    # -- computes ---------------------------------------------------------- #

    @api.depends('model')
    def _compute_x_res_model_id(self):
        """Resolve the `model` char into a real relation.

        Odoo's own pointer stays the source of truth; this is a stored
        denormalisation whose only job is to make group-by and the search panel
        possible, neither of which can bind to a char that holds a model name.
        """
        models_by_name = {}
        for message in self:
            name = message.model
            if not name:
                message.x_res_model_id = False
                continue
            if name not in models_by_name:
                models_by_name[name] = self.env['ir.model']._get(name)
            message.x_res_model_id = models_by_name[name]

    def _compute_x_document_name(self):
        """Name of the linked record, batched per model.

        Not compute_sudo: a user who cannot read the document must not learn
        its name from the lens. `(no access)` is the honest answer, and the
        list stays readable rather than raising halfway through rendering.
        """
        by_model = {}
        for message in self:
            message.x_document_name = False
            if message.model and message.res_id:
                by_model.setdefault(message.model, []).append(message)

        for model_name, messages in by_model.items():
            if model_name not in self.env:
                # The model was uninstalled; the messages outlived it.
                continue
            records = self.env[model_name].browse(
                [m.res_id for m in messages]
            ).exists()
            names = {}
            try:
                names = {r.id: r.display_name for r in records}
            except Exception:
                # Access error, or a display_name that raises on a broken row.
                for message in messages:
                    message.x_document_name = _('(no access)')
                continue
            for message in messages:
                message.x_document_name = names.get(message.res_id) or False

    @api.depends('notification_ids.notification_status')
    def _compute_x_delivery_state(self):
        """Aggregate mail.notification, rather than storing a fourth copy.

        Delivery truth is already split three ways in Odoo: mail.mail holds it
        until it is deleted on success, mail.message holds the content, and
        mail.notification holds the per-recipient outcome. Storing a column
        here would add a fourth that can drift. One read_group answers it for
        the whole recordset.
        """
        self.x_delivery_state = False
        if not self:
            return
        grouped = self.env['mail.notification']._read_group(
            [('mail_message_id', 'in', self.ids)],
            groupby=['mail_message_id', 'notification_status'],
            aggregates=['__count'],
        )
        statuses = {}
        for message, status, count in grouped:
            statuses.setdefault(message.id, {})[status] = count

        for message in self:
            counts = statuses.get(message.id)
            if not counts:
                message.x_delivery_state = False
            elif counts.get('exception') or counts.get('bounce'):
                message.x_delivery_state = 'failed'
            elif counts.get('ready'):
                message.x_delivery_state = 'pending'
            elif counts.get('sent'):
                message.x_delivery_state = 'sent'
            else:
                message.x_delivery_state = False

    def _search_x_delivery_state(self, operator, value):
        """Make the computed state searchable, and keep it consistent.

        A compute without a search silently drops the filter from any domain,
        which in a lens reads as "no failures" rather than "not filtered".
        """
        if operator not in ('=', '!=', 'in', 'not in'):
            raise UserError(_('Unsupported operator for Delivery: %s') % operator)

        # Odoo 19 normalises the domain before calling this: ('=', 'failed')
        # arrives as ('in', OrderedSet(['failed'])). Testing for list/tuple
        # therefore wraps the set instead of unpacking it, and the next line
        # tries to hash it. Accept any non-string iterable.
        if isinstance(value, str) or not hasattr(value, '__iter__'):
            values = [value]
        else:
            values = list(value)
        negate = operator in ('!=', 'not in')

        status_map = {
            'failed': ['exception', 'bounce'],
            'pending': ['ready'],
            'sent': ['sent'],
        }
        statuses = [s for v in values for s in status_map.get(v, [])]
        if not statuses:
            return [(1, '=', 1)] if negate else [(0, '=', 1)]
        leaf = ('notification_ids.notification_status', 'in', statuses)
        return ['!', leaf] if negate else [leaf]

    # -- navigation -------------------------------------------------------- #

    def action_open_document(self):
        """Open the record this message is filed under.

        A char plus an action rather than a fields.Reference: Reference would
        add a third stored copy of (model, res_id) to the biggest table in the
        database, double the writes, and still group on the raw "crm.lead,42"
        string. This is also how mail.activity and ir.attachment navigate.

        Not sudo'd on purpose — a user without access to the document should
        get the AccessError, not a window onto a record they may not read.
        """
        self.ensure_one()
        if not self.model or not self.res_id:
            raise UserError(_('This message is not linked to a document.'))
        if self.model not in self.env:
            raise UserError(_('The model %s is no longer installed.') % self.model)
        record = self.env[self.model].browse(self.res_id)
        if not record.exists():
            raise UserError(_('The linked document no longer exists.'))
        return {
            'type': 'ir.actions.act_window',
            'res_model': self.model,
            'res_id': self.res_id,
            'view_mode': 'form',
            'views': [(False, 'form')],
            'target': 'current',
        }
