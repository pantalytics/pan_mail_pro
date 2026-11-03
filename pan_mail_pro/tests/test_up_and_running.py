# -*- coding: utf-8 -*-
"""The end of onboarding: "Mail Pro is up and running", once (#391).

It fires when all four checklist dots are green and the first incoming mail
has landed on a record, the platform's station 4. A green step 4 alone is not
the end. The confetti is once per database, a config parameter, and the one
next action opens the colleagues who have not connected a mailbox yet.
"""
from odoo.exceptions import AccessError
from odoo.tests import tagged

from odoo.addons.pan_mail_pro.models.pan_mail_setup import CELEBRATED_PARAM

from .common import MailProTestCase


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestUpAndRunning(MailProTestCase):

    def setUp(self):
        super().setUp()
        self.Setup = self.env['pan.mail.setup']
        self.Params = self.env['ir.config_parameter'].sudo()
        # Step 1: an entitled link (the suite's gate says yes, tests/connected.py).
        self.link = self.env['pan.mail.license'].create({'status': 'active'})
        # Step 2: Microsoft 365 through the Pantalytics app, with people
        # signed in (the fixture connects three).
        self.provider = self.env['pan.mail.provider'].create({
            'provider': 'outlook', 'registration': 'pantalytics',
            'pantalytics_client_id': 'app-id'})
        # Step 4: the fixture's notification mailbox is shared, and its
        # owner's sign-in has been seen to read and send from it.
        self.owner_account = self.notif_owner.x_pan_mail_account_ids
        self.env['pan.mail.mailbox.access'].note_send(
            self.notification_mailbox, self.owner_account, True)
        self.lead = self.env['crm.lead'].create({'name': 'Asafdichtingen'})

    def mail(self, model, res_id, direction='incoming'):
        return self.env['mail.message'].sudo().create({
            'model': model, 'res_id': res_id, 'message_type': 'email',
            'body': '<p>Hello</p>', 'x_direction': direction,
            'x_mailbox_id': self.shared_mailbox.id})

    def on_a_record(self):
        return self.mail('crm.lead', self.lead.id)

    def settings(self):
        return self.env['res.config.settings'].create({})

    # ------------------------------------------------------------- the dots

    def test_four_green_dots(self):
        self.assertEqual(self.Setup.step_dots(), {
            'account': 'ok', 'provider': 'ok', 'domains': 'ok', 'mailboxes': 'ok'})
        settings = self.settings()
        self.assertEqual(
            (settings.x_account_dot, settings.x_provider_dot,
             settings.x_domains_dot, settings.x_mailboxes_dot),
            ('ok', 'ok', 'ok', 'ok'))

    def test_each_dot_keeps_the_colour_the_page_drew(self):
        """The rules moved out of the view's `invisible` expressions; these
        are the shapes each colour was written for."""
        self.link.status = 'pending'
        self.assertEqual(self.Setup.step_dots()['account'], 'todo')
        self.link.status = 'revoked'
        real_gate = self.Setup.with_context(pan_mail_pro_real_gate=True)
        self.assertEqual(real_gate.step_dots()['account'], 'warn', 'connected, then broke')
        self.link.status = 'active'

        self.provider.registration = 'own'
        self.assertEqual(self.Setup.step_dots()['provider'], 'attention', 'an own Azure app')
        self.provider.unlink()
        self.assertEqual(self.Setup.step_dots()['provider'], 'todo')

        self.personal_mailbox.is_notification_mailbox = True
        self.assertEqual(self.Setup.step_dots()['mailboxes'], 'attention',
                         'a personal mailbox carries the system email')
        self.notification_mailbox.is_notification_mailbox = True
        self.assertEqual(self.Setup.step_dots()['mailboxes'], 'ok')
        self.env['pan.mail.mailbox.access'].note_send(
            self.notification_mailbox, self.owner_account, False, error='refused')
        self.assertEqual(self.Setup.step_dots()['mailboxes'], 'warn')

    def test_a_stopped_mailbox_is_red(self):
        self.shared_mailbox.sudo().state = 'error'
        self.assertEqual(self.Setup.step_dots()['mailboxes'], 'warn')

    def test_the_view_draws_the_word(self):
        """One rule: every dot on the page reads its step's word."""
        arch = self.env['res.config.settings'].get_view(
            self.env.ref('pan_mail_pro.res_config_settings_view_form').id, 'form')['arch']
        for step in ('account', 'provider', 'domains', 'mailboxes'):
            self.assertIn(f"x_{step}_dot != 'ok'", arch)
        self.assertNotIn('x_notification_access_state != \'ok\'', arch)
        self.assertIn('name="mailpro_up_and_running"', arch)

    # ------------------------------------------------ the first mail on a record

    def test_green_is_not_the_end_without_a_mail_on_a_record(self):
        self.assertFalse(self.Setup.first_mail_on_record())
        self.assertFalse(self.Setup.up_and_running())
        self.assertFalse(self.settings().x_up_and_running)
        self.on_a_record()
        self.assertTrue(self.Setup.up_and_running())
        self.assertTrue(self.settings().x_up_and_running)

    def test_a_contact_an_outgoing_mail_or_a_note_is_not_it(self):
        self.mail('res.partner', self.external_partner.id)
        self.mail('crm.lead', self.lead.id, direction='outgoing')
        self.env['mail.message'].sudo().create({
            'model': 'crm.lead', 'res_id': self.lead.id,
            'message_type': 'comment', 'body': '<p>A note</p>'})
        self.assertFalse(self.Setup.first_mail_on_record())

    def test_it_is_what_link_coverage_calls_linked(self):
        """One reading of "on a record": the coverage report's."""
        self.on_a_record()
        self.mail('res.partner', self.external_partner.id)
        counts = self.env['pan.mail.coverage'].counts_since('2000-01-01')
        linked = self.env['mail.message'].sudo().search_count(
            [('x_direction', '!=', False)] + self.env['pan.mail.coverage']._linked_domain())
        self.assertEqual(counts['linked'], linked)

    def test_a_red_step_later_opens_the_checklist_again(self):
        """Not sticky: the line comes back where the red dot is."""
        self.on_a_record()
        self.assertTrue(self.Setup.up_and_running())
        self.shared_mailbox.sudo().state = 'error'
        self.assertFalse(self.Setup.up_and_running())

    # ----------------------------------------------------------- the confetti

    def test_confetti_once_per_database(self):
        self.on_a_record()
        self.assertFalse(self.Params.get_param(CELEBRATED_PARAM))
        self.assertTrue(self.Setup.celebrate_once())
        self.assertTrue(self.Params.get_param(CELEBRATED_PARAM))
        self.assertFalse(self.Setup.celebrate_once(), 'a reload is not a second time')
        self.assertFalse(self.Setup.with_user(self.env.ref('base.user_admin')).celebrate_once())

    def test_no_confetti_before_the_end(self):
        """A call before the line is done cannot spend the moment."""
        self.assertFalse(self.Setup.celebrate_once())
        self.assertFalse(self.Params.get_param(CELEBRATED_PARAM))

    def test_only_an_administrator_spends_it(self):
        self.on_a_record()
        with self.assertRaises(AccessError):
            self.Setup.with_user(self.salesperson).celebrate_once()
        self.assertFalse(self.Params.get_param(CELEBRATED_PARAM))

    # ------------------------------------------------------- the next action

    def test_invite_opens_the_colleagues_with_no_mailbox(self):
        action = self.Setup.action_invite_colleagues()
        self.assertEqual(action['res_model'], 'res.users')
        users = self.env['res.users'].search(action['domain'])
        self.assertIn(self.inbox_user, users, 'internal, nothing connected')
        for absent in (self.notif_owner, self.salesperson, self.portal_user):
            self.assertNotIn(absent, users)
        self.assertEqual(self.settings().x_unconnected_count,
                         self.env['res.users'].sudo().search_count(action['domain']))

    def test_the_invite_on_that_list_is_the_existing_nudge(self):
        """The list the action opens carries Send Mail Pro invite, which mails
        the one-click connect link to whoever it would help."""
        arch = self.env['res.users'].get_view(
            self.env.ref('base.view_users_tree').id, 'list')['arch']
        self.assertIn('action_send_connect_invite', arch)
