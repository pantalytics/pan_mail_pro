# -*- coding: utf-8 -*-
"""The end of onboarding: "Mail Pro is up and running", once (#391, #415).

It fires when the five checklist steps are done: green, or amber on step 5,
a personal mailbox carrying the system email. The checklist is
configuration only, so it does not wait for the first mail on a record; the
heartbeat's `setup.done` tells the Pantalytics app, which walks the person
to it. The confetti is once per database, a config parameter, and the one
next action opens the colleagues who have not connected a mailbox yet.
"""
from odoo.exceptions import AccessError
from odoo.tests import tagged

from odoo.addons.pan_mail_pro.models.pan_mail_setup import CELEBRATED_PARAM

from .common import MailProTestCase

STEPS = ('account', 'provider', 'domains', 'mailboxes', 'notification')


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
        # Steps 4 and 5: the fixture's notification mailbox is shared, it
        # works, and its owner's sign-in has been seen to read and send from it.
        self.owner_account = self.notif_owner.x_pan_mail_account_ids
        self.env['pan.mail.mailbox.access'].note_send(
            self.notification_mailbox, self.owner_account, True)

    def settings(self):
        return self.env['res.config.settings'].create({})

    # ------------------------------------------------------------- the dots

    def test_five_green_dots(self):
        self.assertEqual(self.Setup.step_dots(), dict.fromkeys(STEPS, 'ok'))
        settings = self.settings()
        self.assertEqual(
            tuple(settings[f'x_{step}_dot'] for step in STEPS), ('ok',) * 5)

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
        self.assertEqual(self.Setup.step_dots()['notification'], 'attention',
                         'a personal mailbox carries the system email')
        self.assertEqual(self.Setup.step_dots()['mailboxes'], 'ok', 'step 4 has no opinion')
        self.notification_mailbox.is_notification_mailbox = True
        self.assertEqual(self.Setup.step_dots()['notification'], 'ok')
        self.env['pan.mail.mailbox.access'].note_send(
            self.notification_mailbox, self.owner_account, False, error='refused')
        self.assertEqual(self.Setup.step_dots()['notification'], 'warn')

    def test_a_stopped_mailbox_is_red_on_step_4(self):
        """A stopped mailbox is step 4's: the notification mailbox still
        sends, so step 5 keeps its own colour."""
        self.shared_mailbox.sudo().state = 'error'
        dots = self.Setup.step_dots()
        self.assertEqual(dots['mailboxes'], 'warn')
        self.assertEqual(dots['notification'], 'ok')

    def test_step_4_wants_one_mailbox_that_works(self):
        """Not stopped and signed in: a mailbox nobody can reach is not one
        that works."""
        self.assertEqual(self.Setup._mailboxes_dot(), 'ok')
        for user in (self.notif_owner, self.other_user, self.salesperson):
            self.disconnect(user)
        self.assertEqual(self.Setup._mailboxes_dot(), 'todo')

    def test_the_view_draws_the_word(self):
        """One rule: every dot on the page reads its step's word."""
        arch = self.env['res.config.settings'].get_view(
            self.env.ref('pan_mail_pro.res_config_settings_view_form').id, 'form')['arch']
        for step in STEPS:
            self.assertIn(f"x_{step}_dot != 'ok'", arch)
        self.assertNotIn('x_notification_access_state != \'ok\'', arch)
        self.assertIn('name="mailpro_up_and_running"', arch)
        for name in ('4. Mailboxes', '5. Notification mailbox'):
            self.assertIn(name, arch)

    # ------------------------------------------------------- the end of the line

    def test_five_done_is_up_and_running_without_a_mail(self):
        """Configuration only (#415): nothing has to have arrived yet."""
        self.assertFalse(self.env['mail.message'].sudo().search_count(
            [('x_direction', '=', 'incoming')]))
        self.assertTrue(self.Setup.up_and_running())
        self.assertTrue(self.settings().x_up_and_running)

    def test_an_amber_step_5_is_done(self):
        """A personal mailbox carrying the system email works; a shared one
        is the recommendation, said under the headline, not a blocker."""
        self.personal_mailbox.is_notification_mailbox = True
        self.assertEqual(self.Setup.step_dots()['notification'], 'attention')
        self.assertTrue(self.Setup.up_and_running())
        settings = self.settings()
        self.assertTrue(settings.x_up_and_running)
        self.assertEqual(settings.x_notification_dot, 'attention', 'the line reads this')

    def test_no_working_mailbox_is_not_done(self):
        for user in (self.notif_owner, self.other_user, self.salesperson):
            self.disconnect(user)
        dots = self.Setup.step_dots()
        self.assertEqual(dots['mailboxes'], 'todo')
        self.assertFalse(self.Setup.up_and_running(dots))
        self.assertFalse(self.settings().x_up_and_running)

    def test_red_or_open_steps_block_and_so_does_another_amber(self):
        """Only step 5's amber is a recommendation; an own Azure app on step
        2 still has its own line to show, so it keeps the checklist open."""
        self.provider.registration = 'own'
        self.assertEqual(self.Setup.step_dots()['provider'], 'attention')
        self.assertFalse(self.Setup.up_and_running())
        self.provider.registration = 'pantalytics'
        self.env['pan.mail.domain'].search([]).unlink()
        self.assertEqual(self.Setup.step_dots()['domains'], 'todo')
        self.assertFalse(self.Setup.up_and_running())

    def test_a_red_step_later_opens_the_checklist_again(self):
        """Not sticky: the line comes back where the red dot is."""
        self.assertTrue(self.Setup.up_and_running())
        self.shared_mailbox.sudo().state = 'error'
        self.assertFalse(self.Setup.up_and_running())

    # ----------------------------------------------------------- the confetti

    def test_confetti_once_per_database(self):
        self.assertFalse(self.Params.get_param(CELEBRATED_PARAM))
        self.assertTrue(self.Setup.celebrate_once())
        self.assertTrue(self.Params.get_param(CELEBRATED_PARAM))
        self.assertFalse(self.Setup.celebrate_once(), 'a reload is not a second time')
        self.assertFalse(self.Setup.with_user(self.env.ref('base.user_admin')).celebrate_once())

    def test_no_confetti_before_the_end(self):
        """A call before the line is done cannot spend the moment."""
        self.env['pan.mail.domain'].search([]).unlink()
        self.assertFalse(self.Setup.celebrate_once())
        self.assertFalse(self.Params.get_param(CELEBRATED_PARAM))

    def test_only_an_administrator_spends_it(self):
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

    # ---------------------------------------------------- the first onchange

    def test_the_first_onchange_computes_the_dots_and_the_line(self):
        """The web client opens Settings with one onchange over every field,
        and 18 computes only what depends on a field that had a default
        there (19 and 20 compute the rest as well). Measured 2026-11-03:
        without a dependency the checklist drew no dots on 18 and the line
        never came."""
        dots = [f'x_{step}_dot' for step in STEPS]
        spec = {name: {} for name in dots + ['x_up_and_running', 'company_id']}
        value = self.env['res.config.settings'].onchange({}, [], spec).get('value', {})
        for name in dots:
            self.assertIn(name, value, f'{name} is not computed by the first onchange')
        self.assertEqual(bool(value.get('x_up_and_running')), self.Setup.up_and_running())
