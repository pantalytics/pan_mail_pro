# -*- coding: utf-8 -*-
"""
Correcting a match, and what the correction buys.

The move itself is the boring half. The half worth testing is the thread link
it writes: after somebody files a conversation by hand, the *next* mail in it
must match at rule 3, exactly, without anyone being asked again. That is the
only part of triage that compounds, and it is invisible from the screen -- so
if it silently stopped happening, nothing would say so.

The rest of these are the three things `link_to` deliberately does not do,
each of which would be a quiet regression rather than a failure: it must not
subscribe anybody to the destination, it must not accept a caller who cannot
write that destination, and it must not move a note.
"""
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestLinkTo(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['pan.mail.domain'].set_domains(['company.test'])
        cls.Log = cls.env['pan.mail.routing.log']
        cls.customer = cls.env['res.partner'].create({
            'name': 'Vandermolen Techniek',
            'email': 'bart@vandermolen.test',
        })
        cls.mailbox = cls.env['pan.mail.mailbox'].create({
            'email': 'support@company.test',
            'provider': 'imap',
            'mailbox_type': 'shared',
        })
        cls.lead = cls.env['crm.lead'].create({
            'name': 'Asafdichtingen',
            'partner_id': cls.customer.id,
        })
        cls.manager_group = cls.env.ref('pan_mail_pro.group_mail_mailbox_manager')
        cls.env.user.sudo().write({'group_ids': [(4, cls.manager_group.id)]})

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    THREAD = '<root@vandermolen.test>'

    def _fallback_mail(self, subject='Storing aan de pers'):
        """A mail that landed on the contact because nothing matched.

        The exact shape the log records for `outcome = fallback`, including
        the thread link, because that link is what the correction repoints.
        """
        message = self.env['mail.message'].create({
            'model': 'res.partner',
            'res_id': self.customer.id,
            'message_type': 'email',
            'subject': subject,
            'body': '<p>De pers loopt vast.</p>',
            'author_id': self.customer.id,
            'email_from': self.customer.email,
            'x_direction': 'incoming',
            'x_mailbox_id': self.mailbox.id,
        })
        self.env['pan.mail.thread.link'].record(
            mailbox=self.mailbox, thread_id=self.THREAD,
            model='res.partner', res_id=self.customer.id,
            message=message, key_type='rfc',
        )
        log = self.Log.sudo().create({
            'mailbox_id': self.mailbox.id,
            'mail_message_id': message.id,
            'outcome': 'fallback',
            'subject': subject,
            'email_from': self.customer.email,
            'thread_id': self.THREAD,
        })
        return message, log

    # ------------------------------------------------------------------ #
    # What the correction buys
    # ------------------------------------------------------------------ #

    def test_link_to_moves_the_message(self):
        message, _log = self._fallback_mail()

        result = self.Log.link_to([message.id], 'crm.lead', self.lead.id)

        self.assertEqual(message.model, 'crm.lead')
        self.assertEqual(message.res_id, self.lead.id)
        self.assertEqual(result['name'], self.lead.display_name)

    def test_link_to_repoints_the_thread_so_the_next_mail_needs_nobody(self):
        """The whole point. One click, and rule 3 answers from then on."""
        message, _log = self._fallback_mail()
        self.Log.link_to([message.id], 'crm.lead', self.lead.id)

        decision = self.env['pan.mail.matcher']._match(
            {
                'message_id': '<second@vandermolen.test>',
                'thread_id': None,
                'subject': 'Re: Storing aan de pers',
                'headers': {'References': self.THREAD},
            },
            mailbox=self.mailbox,
        )

        self.assertEqual(decision['model'], 'crm.lead')
        self.assertEqual(decision['res_id'], self.lead.id)
        self.assertEqual(decision['rule'], 'thread_link')

    def test_link_to_marks_the_row_reviewed_and_drops_the_suggestion(self):
        """A corrected row is answered, so it stops asking."""
        message, log = self._fallback_mail()
        log.write({
            'suggested_model': 'crm.lead',
            'suggested_res_id': self.lead.id,
            'suggested_name': self.lead.display_name,
        })

        self.Log.link_to([message.id], 'crm.lead', self.lead.id)

        self.assertTrue(log.reviewed)
        self.assertTrue(log.corrected_at)
        self.assertFalse(log.suggested_model)

    def test_a_correction_is_counted_against_the_rule_it_overruled(self):
        """What the heartbeat reports per rule: decided, and overruled by hand."""
        message, log = self._fallback_mail()
        log.write({'rule': 'subject_participants'})
        since = fields.Datetime.now() - timedelta(hours=1)
        before = {r['rule']: r for r in self.Log.rule_counts_since(since)}
        self.assertEqual(before['subject_participants']['corrected'], 0)

        self.Log.link_to([message.id], 'crm.lead', self.lead.id)

        after = {r['rule']: r for r in self.Log.rule_counts_since(since)}
        self.assertEqual(after['subject_participants']['wins'], 1)
        self.assertEqual(after['subject_participants']['corrected'], 1)
        for row in after.values():
            self.assertEqual(set(row), {'rule', 'wins', 'corrected'})

    def test_a_fallback_counts_under_none(self):
        self._fallback_mail()
        since = fields.Datetime.now() - timedelta(hours=1)
        rows = {r['rule']: r for r in self.Log.rule_counts_since(since)}
        self.assertGreaterEqual(rows['none']['wins'], 1)

    def test_link_to_moves_the_whole_conversation_it_is_given(self):
        """Leaving half a thread behind splits it across two records."""
        first, _log = self._fallback_mail()
        second, _log2 = self._fallback_mail(subject='Re: Storing aan de pers')

        self.Log.link_to([first.id, second.id], 'crm.lead', self.lead.id)

        self.assertEqual(first.res_id, self.lead.id)
        self.assertEqual(second.res_id, self.lead.id)

    # ------------------------------------------------------------------ #
    # What it must not do
    # ------------------------------------------------------------------ #

    def test_link_to_subscribes_nobody(self):
        """Linking mail must not become a way to start notifying people.

        The same rule CC follows: a message arriving on a record is not a
        reason to put its author on that record's follower list.
        """
        message, _log = self._fallback_mail()
        before = set(self.lead.message_partner_ids.ids)

        self.Log.link_to([message.id], 'crm.lead', self.lead.id)

        self.assertEqual(set(self.lead.message_partner_ids.ids), before)

    def test_link_to_refuses_a_note(self):
        """Only correspondence is linked. A note belongs to its record."""
        note = self.env['mail.message'].create({
            'model': 'res.partner',
            'res_id': self.customer.id,
            'message_type': 'comment',
            'body': '<p>gebeld</p>',
        })
        with self.assertRaises(UserError):
            self.Log.link_to([note.id], 'crm.lead', self.lead.id)
        self.assertEqual(note.model, 'res.partner')

    def test_link_to_refuses_a_destination_that_is_gone(self):
        message, _log = self._fallback_mail()
        missing = self.lead.id
        self.lead.unlink()

        with self.assertRaises(UserError):
            self.Log.link_to([message.id], 'crm.lead', missing)

    def test_link_to_is_for_mailbox_managers(self):
        """The menu carries the group; the method has to carry it too.

        `link_to` answers `call_kw` from any session, so the group on the
        inbox's menu protects nothing on its own.
        """
        message, _log = self._fallback_mail()
        stranger = self.env['res.users'].create({
            'name': 'Buitenstaander',
            'login': 'linking-stranger',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })

        with self.assertRaises(AccessError):
            self.Log.with_user(stranger).link_to([message.id], 'crm.lead', self.lead.id)
        self.assertEqual(message.model, 'res.partner')


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestLinkPicker(TransactionCase):
    """The two steps of the picker: which kind of record, then which record.

    Step one is this module's own list of models. Step two is Odoo's own search
    dialog, so all that is left here is the head start it opens on -- the
    correspondent's records, as a search facet -- which is the only part of
    this screen that saves anybody a search.

    Both answer over RPC, so both are reachable by anyone with a session, and
    both take a model name from the caller.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['pan.mail.domain'].set_domains(['company.test'])
        cls.Conversation = cls.env['pan.mail.conversation']
        cls.customer = cls.env['res.partner'].create({
            'name': 'Vandermolen Techniek',
            'email': 'info@vandermolen.test',
        })
        cls.contact = cls.env['res.partner'].create({
            'name': 'Bart Vandermolen',
            'email': 'bart@vandermolen.test',
            'parent_id': cls.customer.id,
        })
        cls.other = cls.env['res.partner'].create({
            'name': 'Asaf BV', 'email': 'inkoop@asaf.test',
        })
        cls.theirs = cls.env['crm.lead'].create({
            'name': 'Pers revisie', 'partner_id': cls.customer.id,
        })
        cls.not_theirs = cls.env['crm.lead'].create({
            'name': 'Asafdichtingen', 'partner_id': cls.other.id,
        })
        cls.env.user.sudo().write({'group_ids': [
            (4, cls.env.ref('pan_mail_pro.group_mail_mailbox_manager').id)]})

    # ------------------------------------------------------- step one: model

    def test_targets_start_from_what_this_database_links_mail_to(self):
        labels = {row['model'] for row in self.Conversation.link_targets()}
        self.assertIn('res.partner', labels)

    def test_the_list_is_filled_with_models_that_know_whose_record_it_is(self):
        """A chatter is not enough, or the picker becomes a directory."""
        models = {row['model'] for row in self.Conversation.link_targets()}
        self.assertIn('crm.lead', models)
        self.assertNotIn('mail.blacklist', models)

    def test_every_model_row_carries_the_apps_own_tile(self):
        """A list of names all ending in "Order" is read by its icons."""
        rows = {row['model']: row for row in self.Conversation.link_targets()}
        self.assertTrue(rows['crm.lead']['icon'])
        self.assertTrue(all('icon' in row for row in rows.values()))

    def test_a_search_widens_past_that_list(self):
        """The model nobody has filed mail on yet is one search away."""
        found = {row['model']
                 for row in self.Conversation.link_targets(search='Lead')}
        self.assertIn('crm.lead', found)

    def test_a_search_offers_what_is_already_linked_first(self):
        rows = self.Conversation.link_targets(search='Contact')
        self.assertEqual(rows[0]['model'], 'res.partner')

    def test_the_picker_is_for_mailbox_managers(self):
        stranger = self.env['res.users'].create({
            'name': 'Buitenstaander', 'login': 'picker-stranger',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        with self.assertRaises(AccessError):
            self.Conversation.with_user(stranger).link_targets()
        with self.assertRaises(AccessError):
            self.Conversation.with_user(stranger).link_candidate_domain('crm.lead')

    # ------------------------------------------- step two: the head start

    def test_the_facet_is_the_correspondents_own_records(self):
        seed = self.Conversation.link_candidate_domain(
            'crm.lead', partner_id=self.contact.id)

        self.assertEqual(seed['description'], self.customer.display_name)
        found = self.env['crm.lead'].search(seed['domain'])
        self.assertIn(self.theirs, found)
        self.assertNotIn(self.not_theirs, found)

    def test_the_company_answers_for_the_person_who_wrote(self):
        """Mail from one employee is about the company's records."""
        seed = self.Conversation.link_candidate_domain(
            'res.partner', partner_id=self.contact.id)

        found = self.env['res.partner'].search(seed['domain'])
        self.assertIn(self.contact, found)
        self.assertIn(self.customer, found)
        self.assertNotIn(self.other, found)

    def test_without_a_correspondent_there_is_no_facet(self):
        """The dialog then opens on its own default, which is a list and a
        search box. Same for a model relating to a contact through anything
        but `partner_id` or `email_from`: that is the dropped case, on
        purpose -- a third relation would be a guess nobody could predict.
        """
        self.assertEqual(self.Conversation.link_candidate_domain('crm.lead'), {})

    def test_the_facet_refuses_a_model_mail_cannot_land_on(self):
        with self.assertRaises(AccessError):
            self.Conversation.link_candidate_domain('ir.config_parameter')
        with self.assertRaises(AccessError):
            self.Conversation.link_candidate_domain('not.a.model')
