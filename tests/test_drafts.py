# -*- coding: utf-8 -*-
"""Drafts: the one thing the Inbox stores.

Three questions, and the file is organised around them. Does a draft hold what
the composer held, so opening it again is carrying on rather than starting
over. Does it stay on its conversation, so sending it changes nothing about
where the mail lands. And is it private, which is the one that cannot be
fixed after the fact: a half-written answer is not correspondence, and the
mailbox managers who may read every mail in a shared mailbox still may not
read what a colleague has not sent.

`test_reopening_keeps_what_was_typed` is the one that catches a silent
regression. The composer resets its own body whenever no template is chosen,
so a draft handed back through anything other than a `default_` would come
back empty -- in the browser, with an empty server log.
"""
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestDrafts(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['pan.mail.domain'].set_domains(['company.test'])
        cls.manager_group = cls.env.ref('pan_mail_pro.group_mail_mailbox_manager')
        cls.Draft = cls.env['pan.mail.draft']
        cls.Conversation = cls.env['pan.mail.conversation']
        cls.customer = cls.env['res.partner'].create({
            'name': 'Vandermolen Techniek',
            'email': 'bart@vandermolen.test',
        })
        cls.mailbox = cls.env['pan.mail.mailbox'].create({
            'email': 'sales@company.test',
            'provider': 'imap',
            'mailbox_type': 'shared',
        })
        cls.lead = cls.env['crm.lead'].create({
            'name': 'Asafdichtingen',
            'partner_id': cls.customer.id,
        })
        cls.user = cls._manager('draft.writer@company.test')
        cls.colleague = cls._manager('draft.colleague@company.test')

    @classmethod
    def _manager(cls, login):
        """Somebody the Inbox is for, who may also open the lead.

        The CRM groups are not decoration, and *all leads* is the one that
        matters: `crm_rule_personal_lead` limits a plain salesman to the
        leads assigned to them, so without it both of these people would be
        unable to read the record their draft sits on -- and this file would
        be testing that refusal instead of the draft. What the privacy tests
        below prove is then the real thing: two people who can see the same
        mailbox and the same lead, and still not each other's drafts.
        """
        return cls.env['res.users'].create({
            'name': login,
            'login': login,
            'email': login,
            'group_ids': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('sales_team.group_sale_salesman').id,
                cls.env.ref('sales_team.group_sale_salesman_all_leads').id,
                cls.manager_group.id])],
        })

    def _draft(self, row):
        """The stored row, read by the person who wrote it -- the only one
        who can: every read here goes through the rule, deliberately."""
        return self.Draft.with_user(self.user).browse(row['draft_id'])

    def _incoming(self, subject='Offerte'):
        return self.env['mail.message'].create({
            'model': 'crm.lead',
            'res_id': self.lead.id,
            'message_type': 'email',
            'subject': subject,
            'body': '<p>Kunnen jullie de levertijd bevestigen?</p>',
            'author_id': self.customer.id,
            'email_from': self.customer.email,
            'x_direction': 'incoming',
            'x_mailbox_id': self.mailbox.id,
        })

    def _composer(self, user=None, **overrides):
        """The wizard the pane holds, as the pane fills it in."""
        values = {
            'model': 'crm.lead',
            'res_ids': repr([self.lead.id]),
            'composition_mode': 'comment',
            'subject': 'Re: Offerte',
            'body': '<p>De levertijd is vier weken.</p>',
            'partner_ids': [(6, 0, self.customer.ids)],
            'x_send_from_mailbox_id': self.mailbox.id,
        }
        values.update(overrides)
        return self.env['mail.compose.message'].with_user(
            user or self.user).create(values)

    def _save(self, composer, draft_id=None, user=None):
        return self.Draft.with_user(user or self.user).save_from_composer(
            composer.id, draft_id=draft_id)

    # ------------------------------------------------------------- storing

    def test_saving_a_composer_stores_what_it_held(self):
        """The draft is the mail that would have been posted, field for field."""
        row = self._save(self._composer())
        draft = self._draft(row)
        self.assertEqual(draft.model, 'crm.lead')
        self.assertEqual(draft.res_id, self.lead.id)
        self.assertEqual(draft.subject, 'Re: Offerte')
        self.assertIn('vier weken', draft.body)
        self.assertEqual(draft.partner_ids, self.customer)
        self.assertEqual(draft.mailbox_id, self.mailbox)
        self.assertEqual(draft.user_id, self.user)

    def test_saving_again_writes_the_same_draft(self):
        """Saving twice is one unsent mail, not two.

        The Drafts folder is only worth opening if what is in it is what you
        left there; a second row per save is the folder nobody trusts.
        """
        first = self._save(self._composer())
        second = self._save(
            self._composer(subject='Re: Offerte (aangepast)'),
            draft_id=first['draft_id'])
        self.assertEqual(first['draft_id'], second['draft_id'])
        self.assertEqual(
            self.Draft.with_user(self.user).search_count(
                [('user_id', '=', self.user.id)]), 1)
        self.assertEqual(self._draft(first).subject, 'Re: Offerte (aangepast)')

    def test_the_link_is_the_conversation_s(self):
        """A draft is filed before it is written, and stays where it was.

        The record and the message it answers come off the composer, so
        sending it later posts on the same record through the same parent --
        the link does not move because a draft happened in between.
        """
        parent = self._incoming()
        row = self._save(self._composer(parent_id=parent.id))
        draft = self._draft(row)
        self.assertEqual((draft.model, draft.res_id),
                         ('crm.lead', self.lead.id))
        self.assertEqual(draft.parent_id, parent)

        context = draft.composer_context()
        self.assertEqual(context['default_model'], 'crm.lead')
        self.assertEqual(context['default_res_ids'], [self.lead.id])
        self.assertEqual(context['default_parent_id'], parent.id)

    def test_reopening_keeps_what_was_typed(self):
        """A draft opened again is the composer it came from.

        `open_composer` is the whole reason this is created on the server
        rather than opened empty on `default_` values: the composer recomputes
        its own body and subject while a form mounts, and a draft handed over
        that way is gone before anybody sees it. Values passed to `create()`
        are protected from their own compute, so this is what the pane mounts
        on -- and the browser check that caught the empty body is the other
        half of this assertion.
        """
        row = self._save(self._composer())
        draft = self._draft(row)
        reopened = self.env['mail.compose.message'].with_user(self.user).browse(
            draft.open_composer())
        self.assertEqual(reopened.subject, 'Re: Offerte')
        self.assertIn('vier weken', reopened.body)
        self.assertEqual(reopened.partner_ids, self.customer)
        self.assertEqual(reopened.x_send_from_mailbox_id, self.mailbox)
        self.assertEqual(reopened.model, 'crm.lead')
        self.assertEqual(reopened._evaluate_res_ids(), [self.lead.id])

    def test_reopening_a_draft_is_not_reopening_somebody_else_s(self):
        """`open_composer` creates a wizard, so it is asked who is asking."""
        row = self._save(self._composer())
        with self.assertRaises(AccessError):
            self.Draft.with_user(self.colleague).browse(
                row['draft_id']).open_composer()

    def test_discarding_removes_it(self):
        row = self._save(self._composer())
        self.Draft.with_user(self.user).discard_draft(row['draft_id'])
        self.assertFalse(self.Draft.browse(row['draft_id']).exists())

    # -------------------------------------------------------------- reading

    def test_the_drafts_folder_lists_and_counts_it(self):
        """The mailbox list's third folder, read from its own table."""
        self._save(self._composer())
        as_user = self.Conversation.with_user(self.user)
        counts = as_user.folder_counts(mailbox_id=self.mailbox.id,
                                       folder='drafts')
        drafts = [entry for entry in counts['folders'] if entry['id'] == 'drafts']
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0]['count'], 1)
        # Every filter in the row is a question about mail that arrived or
        # went out, and none of them is one about your own unsent answer.
        self.assertEqual(counts['filters'], [])

        rows = as_user.search_conversations(mailbox_id=self.mailbox.id,
                                            folder='drafts')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['subject'], 'Re: Offerte')
        self.assertEqual(rows[0]['record_name'], self.lead.display_name)
        # The list draws one kind of row, so a draft has to be one.
        for key in ('model', 'res_id', 'message_id', 'preview', 'correspondent',
                    'date', 'count', 'unread', 'mailbox'):
            self.assertIn(key, rows[0])

    def test_a_record_that_is_gone_leaves_the_folder_standing(self):
        """The record's name is a label on a row, never a reason to fail.

        A record can be deleted under a draft, and the author can lose access
        to the model in between. Both answer the same way: the draft is still
        theirs, still says what they typed, and the Drafts folder still
        opens.
        """
        self._save(self._composer())
        self.lead.unlink()
        rows = self.Conversation.with_user(self.user).search_conversations(
            mailbox_id=self.mailbox.id, folder='drafts')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['record_name'], '')
        self.assertEqual(rows[0]['subject'], 'Re: Offerte')

    def test_the_draft_is_on_its_conversation(self):
        """Above the thread it answers, where the person left it."""
        self._incoming()
        self._save(self._composer())
        conversation = self.Conversation.with_user(self.user).read_conversation(
            'crm.lead', self.lead.id, mailbox_id=self.mailbox.id)
        self.assertEqual(len(conversation['drafts']), 1)
        self.assertEqual(conversation['drafts'][0]['subject'], 'Re: Offerte')

    # -------------------------------------------------------------- privacy

    def test_a_colleague_sees_nothing_of_it(self):
        """The rule this model exists to carry.

        Both of these people may read the mailbox and every mail in it. An
        answer one of them has not sent is not mail yet, so it is not theirs
        to read -- not in the folder, not on the conversation, and not by id.
        """
        row = self._save(self._composer())
        self._incoming()

        as_colleague = self.Conversation.with_user(self.colleague)
        self.assertEqual(
            as_colleague.search_conversations(mailbox_id=self.mailbox.id,
                                              folder='drafts'), [])
        conversation = as_colleague.read_conversation(
            'crm.lead', self.lead.id, mailbox_id=self.mailbox.id)
        self.assertEqual(conversation['drafts'], [])
        with self.assertRaises(AccessError):
            self.Draft.with_user(self.colleague).browse(
                row['draft_id']).read(['subject'])

    def test_a_colleague_cannot_write_it_either(self):
        """Reading is not the only way to reach somebody's words."""
        row = self._save(self._composer())
        with self.assertRaises(AccessError):
            self._save(self._composer(user=self.colleague),
                       draft_id=row['draft_id'], user=self.colleague)
        with self.assertRaises(AccessError):
            self.Draft.with_user(self.colleague).discard_draft(row['draft_id'])
