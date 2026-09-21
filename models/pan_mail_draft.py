# -*- coding: utf-8 -*-
"""
A draft: the one fact the Inbox keeps.

`pan.mail.conversation` stores nothing, and says at length why. A draft is the
exception, and it is one on purpose: an unsent mail is the only thing on that
screen that exists nowhere else. The chatter has no row for it, `mail.message`
has none either -- a message people can read is a message that went out -- and
the composer is a transient wizard that is gone the moment the pane closes.

So it is a table, and every decision on it is about keeping it from becoming a
second inbox to empty:

- **It is private.** One record rule on `user_id`, for every operation and
  every group, mailbox managers included. A half-written answer is not
  correspondence yet, and nobody gets to read somebody else's.
- **It is filed before it is written.** `model` and `res_id` are required, so a
  draft always sits on the record its mail will land on -- the same question
  Reply and New Email already answer before the composer opens. That is what
  makes sending it a non-event: the draft is on the conversation, so the link
  it goes out under is the link it was saved under, and the matcher has
  nothing to decide.
- **It never sends itself.** No state field, no cron, no queue. A draft leaves
  this table two ways: the person opens it and sends it, which posts through
  Odoo's own composer, or the person deletes it.
- **It is not the provider's draft.** `mail.provider.client.save_draft` puts a
  complete MIME message in the mailbox's own Drafts folder, for another client
  to finish. This row is an Odoo composer somebody closed. Pushing it to
  Outlook as well would leave two half-written copies of one answer and
  nothing to settle which is newer, so this table stays in Odoo.
"""
from odoo import models, fields, api, _
from odoo.exceptions import AccessError, UserError

# What the Drafts folder hands back in one page, and the ceiling on what a
# caller may ask for. Same numbers as the conversation list, for the same
# reason: `limit` arrives over RPC.
DEFAULT_LIMIT = 30
MAX_LIMIT = 200


class PanMailDraft(models.Model):
    """One saved composer, private to the person who wrote it."""

    _name = 'pan.mail.draft'
    _description = 'Mail Pro Draft'
    # Newest first, which for a draft means most recently edited: the one you
    # were writing when the phone rang is the one you came back for.
    _order = 'write_date desc, id desc'

    user_id = fields.Many2one(
        'res.users', string='Author', required=True, index=True,
        ondelete='cascade', default=lambda self: self.env.user)
    mailbox_id = fields.Many2one(
        'pan.mail.mailbox', string='Send from', index=True,
        help="The mailbox this draft will be sent from, and the one whose "
             "Drafts folder lists it.")
    model = fields.Char(string='Document Model', required=True)
    res_id = fields.Many2oneReference(
        string='Document', model_field='model', required=True)
    parent_id = fields.Many2one(
        'mail.message', string='Answered message', ondelete='set null',
        help="The message this draft replies to. It is what threads the "
             "answer for the recipient's mail client.")
    subject = fields.Char()
    body = fields.Html(sanitize_style=True)
    partner_ids = fields.Many2many('res.partner', string='To')
    attachment_ids = fields.Many2many('ir.attachment', string='Attachments')

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    @api.model
    def save_from_composer(self, composer_id, draft_id=None):
        """Store the composer the pane is holding, as a draft.

        The values come off the wizard rather than off the screen. The client
        saves the composer first -- which it does before sending anyway -- and
        hands over its id, so a draft is exactly the record that would have
        been posted: the same subject, the same recipients, the same uploaded
        files, and no second reading of the form in JavaScript to drift from
        it.

        Returns:
            dict: the row the Inbox draws, so the screen needs no second read.
        """
        composer = self.env['mail.compose.message'].browse(
            int(composer_id)).exists()
        if not composer:
            raise UserError(_("This message is no longer open."))
        res_ids = composer._evaluate_res_ids()
        if not composer.model or not res_ids:
            # Every composer this screen opens is on a record: Reply takes
            # the conversation's, New Email asks for one before it opens.
            raise UserError(_("A draft is saved on the record it is written on."))
        vals = {
            # Which mailbox this will be sent from is also which mailbox's
            # Drafts folder lists it, so an empty dropdown falls back to the
            # person's own default -- which is the mailbox the send would
            # have picked anyway. Without the fallback a draft saved with no
            # sender is a draft that is in no folder at all.
            'mailbox_id': (composer.x_send_from_mailbox_id.id
                           or self.env.user.x_default_mailbox_id.id or False),
            'subject': composer.subject or False,
            'body': composer.body or False,
            'partner_ids': [(6, 0, composer.partner_ids.ids)],
            'attachment_ids': [(6, 0, composer.attachment_ids.ids)],
        }
        draft = self._for_editing(draft_id)
        if draft:
            draft.write(vals)
        else:
            draft = self.create(dict(
                vals,
                model=composer.model,
                res_id=res_ids[0],
                parent_id=composer.parent_id.id or False,
            ))
        draft._own_attachments()
        return draft._row()

    @api.model
    def discard_draft(self, draft_id):
        """Delete one draft, and the files that were only ever on it."""
        draft = self._for_editing(draft_id)
        if not draft:
            return False
        draft.attachment_ids.filtered(
            lambda a: a.res_model == draft._name and a.res_id == draft.id
        ).unlink()
        draft.unlink()
        return True

    def _for_editing(self, draft_id):
        """The draft this call may touch, or an empty recordset.

        Browsed and read as the caller, so the record rule is what answers
        "is this yours" -- there is no second implementation of that here.
        """
        if not draft_id:
            return self.browse()
        draft = self.browse(int(draft_id)).exists()
        # Reading a field is what makes the rule run, and a draft somebody
        # else owns raises rather than silently becoming a new one.
        draft.mapped('subject')
        return draft

    def _own_attachments(self):
        """Take the composer's files onto the draft.

        An attachment an upload made belongs to `mail.compose.message` and to
        a wizard that is about to be dropped. Re-pointing it at the draft is
        what keeps the file for as long as the draft keeps the words -- and it
        is what makes the draft's own record rule decide who may read it.
        """
        for draft in self:
            loose = draft.attachment_ids.filtered(
                lambda a: a.res_model in (False, 'mail.compose.message'))
            if loose:
                loose.write({'res_model': draft._name, 'res_id': draft.id})

    # ------------------------------------------------------------------
    # Reading: the Drafts folder, and the card on a conversation
    # ------------------------------------------------------------------

    @api.model
    def folder_count(self, mailbox_id=None, search=None, cap=99):
        """How many drafts stand under this mailbox. Capped like the folders."""
        total = self.search_count(
            self._domain(mailbox_id, search), limit=cap + 1)
        return {'count': min(total, cap), 'capped': total > cap}

    @api.model
    def folder_rows(self, mailbox_id=None, search=None, record_model=None,
                    record_id=None, limit=DEFAULT_LIMIT, offset=0):
        """One page of the Drafts folder, shaped like a conversation row.

        The list pane draws one kind of row. A draft carries `draft_id` and
        nothing else new, so clicking it opens the conversation it belongs to
        with the composer already on it.
        """
        try:
            limit = max(min(int(limit or DEFAULT_LIMIT), MAX_LIMIT), 1)
        except (TypeError, ValueError):
            limit = DEFAULT_LIMIT
        try:
            offset = max(int(offset or 0), 0)
        except (TypeError, ValueError):
            offset = 0
        domain = self._domain(mailbox_id, search)
        if record_model and record_id:
            domain += [('model', '=', record_model),
                       ('res_id', '=', int(record_id))]
        return [draft._row()
                for draft in self.search(domain, limit=limit, offset=offset)]

    @api.model
    def rows_for(self, model, res_id):
        """The drafts on one conversation, for the card above its messages."""
        if not model or not res_id:
            return []
        return [draft._row() for draft in self.search(
            [('model', '=', model), ('res_id', '=', int(res_id))])]

    def _domain(self, mailbox_id=None, search=None):
        """Own drafts, in one mailbox, matching what was typed.

        The rule already limits this to the caller's own rows; naming
        `user_id` here as well is what keeps the count honest for an
        administrator, whose rules Odoo does not apply.
        """
        domain = [('user_id', '=', self.env.uid)]
        if mailbox_id:
            domain.append(('mailbox_id', '=', int(mailbox_id)))
        if search:
            domain += ['|', ('subject', 'ilike', search),
                       ('partner_ids.name', 'ilike', search)]
        return domain

    def _row(self):
        """One draft, in the shape the conversation list already draws."""
        self.ensure_one()
        to = self.partner_ids[:3].mapped('display_name')
        return {
            'draft_id': self.id,
            'model': self.model,
            'res_id': self.res_id,
            # The list keys its rows on the newest message of a conversation;
            # a draft has none, and its own id is what makes the row unique.
            'message_id': False,
            'subject': self.subject or _('(no subject)'),
            'preview': self.env['pan.mail.conversation']._preview(self.body),
            'correspondent': ', '.join(to) or _('No recipient yet'),
            'partner_id': self.partner_ids[:1].commercial_partner_id.id or False,
            'date': self.write_date,
            'count': 1,
            'record_name': self._record_name(),
            'unread': False,
            'mailbox': self.mailbox_id.email or '',
        }

    def _record_name(self):
        """What the record is called, or nothing.

        A name is a label on a row, never a reason for the Drafts folder to
        fail to open: the record may have been deleted since, and the person
        may have lost access to the model in between. Both answer the same
        way -- the draft is still theirs and still says what they typed.
        """
        if self.model not in self.env:
            return ''
        try:
            record = self.env[self.model].browse(self.res_id)
            return record.display_name if record.exists() else ''
        except AccessError:
            return ''

    def open_composer(self):
        """Make the wizard this draft reopens in, and say which one it is.

        The composer is **created here**, not filled in from the screen, and
        that is the whole point. `_compute_body` resets the body whenever no
        template is chosen and `_compute_subject` reaches for the parent's,
        so a form opened empty on `default_` values recomputes a draft away
        while it mounts -- in the browser, with an empty server log. Values
        passed to `create()` are protected from their own compute, so the
        record that comes back already holds what was typed and the form has
        only to display it.

        Returns:
            int: the `mail.compose.message` id the pane mounts its form on.
        """
        self.ensure_one()
        composer = self.env['mail.compose.message'].with_context(
            **self.composer_context()).create({})
        return composer.id

    def composer_context(self):
        """This draft as the composer's own defaults, field for field.

        Read by `open_composer`, which is the only caller: the form is mounted
        on the record that comes out of it rather than on this context. It
        stays a method of its own because it is also what a test can read to
        see what a draft promises to restore.
        """
        self.ensure_one()
        return {
            'default_model': self.model,
            'default_res_ids': [self.res_id],
            'default_composition_mode': 'comment',
            'default_subtype_xmlid': 'mail.mt_comment',
            'default_subject': self.subject or False,
            'default_body': self.body or False,
            'default_partner_ids': self.partner_ids.ids,
            'default_attachment_ids': self.attachment_ids.ids,
            'default_parent_id': self.parent_id.id or False,
            'default_x_send_from_mailbox_id': self.mailbox_id.id or False,
        }
