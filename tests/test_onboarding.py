# -*- coding: utf-8 -*-
"""
Onboarding: the order in which Mail Pro can be set up.

Setting it up used to have a dead zone. Installing the module disabled every
outgoing mail server immediately, and routing through the provider needs an app
registration plus a working notification mailbox — so between install and a
finished setup, the database could not send anything at all. That includes the
user invitations an admin needs to send *in order to* finish the setup, because
users have to authorize their own accounts.

Two changes are pinned here:
- SMTP is taken over when the first mailbox is created, not at install
- internal notifications are queued, not cancelled, while notifications@ is
  still missing
"""
from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged

from odoo.addons.pan_mail_pro.models.mail_mail import NOTIFICATION_PENDING_REASON


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestSmtpTakeover(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Mail Pro refuses to create a mailbox while the internal domain
        # list is empty. A domain nothing in this fixture uses, so the gate
        # opens without turning any fixture address internal.
        cls.env['pan.mail.domain'].set_domains(['gate-fixture.test'])
        cls.Mailbox = cls.env['pan.mail.mailbox']
        cls.MailServer = cls.env['ir.mail_server'].with_context(active_test=False)
        cls.placeholder = cls.env.ref('pan_mail_pro.mail_server_disabled')
        cls.env['ir.config_parameter'].sudo().set_param(
            'pan_mail_pro.smtp_takeover_done', 'False'
        )
        cls.placeholder.active = False
        cls.customer_smtp = cls.MailServer.create({
            'name': 'Customer SMTP', 'smtp_host': 'smtp.example.com',
            'smtp_port': 587, 'sequence': 10,
        })

    def test_smtp_survives_until_the_first_mailbox(self):
        """An admin must be able to invite users before Mail Pro is configured."""
        self.assertTrue(self.customer_smtp.active)
        self.assertFalse(self.placeholder.active)

    def test_first_mailbox_takes_over_smtp(self):
        self.Mailbox.create({'email': 'info@takeover.test', 'mailbox_type': 'shared'})

        self.assertFalse(self.customer_smtp.active,
                         "customer SMTP must be disabled once routing is live")
        self.assertTrue(self.placeholder.active)

    def test_takeover_runs_once(self):
        """A second mailbox must not re-disable a server an admin re-enabled."""
        self.Mailbox.create({'email': 'info@takeover.test', 'mailbox_type': 'shared'})
        self.customer_smtp.active = True

        self.Mailbox.create({'email': 'sales@takeover.test', 'mailbox_type': 'shared'})

        self.assertTrue(self.customer_smtp.active)


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestNotificationGapQueuesMail(TransactionCase):
    """The window between the first mailbox and a working notifications@."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Mail Pro refuses to create a mailbox while the internal domain
        # list is empty. A domain nothing in this fixture uses, so the gate
        # opens without turning any fixture address internal.
        cls.env['pan.mail.domain'].set_domains(['gate-fixture.test'])
        cls.colleague = cls.env['res.users'].create({
            'name': 'Colleague', 'login': 'colleague@gap.test',
            'email': 'colleague@gap.test', 'notification_type': 'email',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])],
        })
        # Mail Pro is switched on (a mailbox exists) but notifications@ is not
        # configured yet — exactly the state an admin is in halfway through.
        cls.env['pan.mail.mailbox'].create({
            'email': 'info@gap.test', 'mailbox_type': 'shared',
        })

    def _notification_to_colleague(self):
        return self.env['mail.mail'].create({
            'subject': 'Invitation',
            'body_html': '<p>Please connect your mailbox</p>',
            'email_to': self.colleague.email,
            'recipient_ids': [(6, 0, [self.colleague.partner_id.id])],
        })

    def test_internal_notification_is_queued_not_cancelled(self):
        mail = self._notification_to_colleague()

        mail.send()

        self.assertEqual(mail.state, 'outgoing',
                         "invitations must survive an unfinished setup")
        self.assertEqual(mail.failure_reason, NOTIFICATION_PENDING_REASON)

    def test_queued_mail_says_why_in_the_mail_queue(self):
        """The settings page no longer counts these. The reason has to be on
        the mail itself, or a held invitation is indistinguishable from a
        failed one for whoever opens the queue."""
        self._notification_to_colleague().send()

        held = self.env['mail.mail'].search([
            ('state', '=', 'outgoing'),
            ('failure_reason', '=', NOTIFICATION_PENDING_REASON),
        ])

        self.assertEqual(len(held), 1)
        self.assertIn('Notification mailbox', held.failure_reason)

    def test_a_chosen_sender_is_not_held_for_the_notification_mailbox(self):
        """The hold is for mail that would take the notification route.

        A mail somebody picked a sender for does not, so it goes out — or
        fails naming that mailbox — rather than waiting on notifications@.
        """
        mailbox = self.env['pan.mail.mailbox'].search(
            [('email', '=', 'info@gap.test')], limit=1)
        mail = self._notification_to_colleague()
        mail.x_send_from_mailbox_id = mailbox

        self.assertFalse(mail._is_awaiting_notification_mailbox())

        with self.assertRaises(UserError):
            mail.send()

        self.assertNotEqual(mail.failure_reason, NOTIFICATION_PENDING_REASON)

    def test_external_mail_still_fails_loudly(self):
        """Only internal notifications get the benefit of the doubt.

        Customer mail must not sit in a queue pretending it will be delivered,
        and must never leak out over SMTP behind the admin's back. An
        unroutable one raises, as before.
        """
        mail = self.env['mail.mail'].create({
            'subject': 'Quotation',
            'body_html': '<p>Here you go</p>',
            'email_to': 'customer@example.com',
        })

        with self.assertRaises(UserError):
            mail.send()

        self.assertNotEqual(mail.failure_reason, NOTIFICATION_PENDING_REASON)


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestSetupChecklist(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = cls.env.user
        cls.env['pan.mail.account'].sudo().create({
            'email': cls.admin.email or cls.admin.login, 'provider': 'outlook',
            'user_id': cls.admin.id,
            'refresh_token': 'fake-refresh', 'access_token': 'fake-access',
        })
        cls.env['pan.mail.domain'].set_domains(['checklist.test'])

    def _settings(self, vals=None):
        return self.env['res.config.settings'].create(vals or {})

    def test_the_notification_mailbox_is_whichever_one_is_ticked(self):
        """Step 5 has no form of its own: it reports the tick box on a mailbox."""
        self.assertFalse(self._settings().x_setup_notification_done)

        mailbox = self.env['pan.mail.mailbox'].create({
            'email': 'notifications@checklist.test',
            'mailbox_type': 'personal',
            'is_notification_mailbox': True,
            'provider': 'outlook',
            'owner_user_id': self.admin.id,
        })

        settings = self._settings()
        self.assertTrue(settings.x_setup_notification_done)
        self.assertEqual(settings.x_notification_mailbox_id, mailbox)

    def test_an_oauth_notification_mailbox_needs_a_connected_owner(self):
        """The step that asked "has anybody signed in" is now this constraint."""
        stranger = self.env['res.users'].create({
            'name': 'Not Connected', 'login': 'stranger@checklist.test',
        })
        with self.assertRaises(ValidationError):
            self.env['pan.mail.mailbox'].create({
                'email': 'notifications@checklist.test',
                'mailbox_type': 'personal',
                'is_notification_mailbox': True,
                'provider': 'outlook',
                'owner_user_id': stranger.id,
            })

    def test_moving_it_is_untick_here_tick_there(self):
        first = self.env['pan.mail.mailbox'].create({
            'email': 'notifications@checklist.test',
            'mailbox_type': 'personal',
            'is_notification_mailbox': True,
            'provider': 'outlook',
            'owner_user_id': self.admin.id,
        })
        second = self.env['pan.mail.mailbox'].create({
            'email': 'system@checklist.test',
            'mailbox_type': 'personal',
            'provider': 'outlook',
            'owner_user_id': self.admin.id,
        })

        with self.assertRaises(ValidationError):
            second.is_notification_mailbox = True

        first.is_notification_mailbox = False
        second.is_notification_mailbox = True
        self.assertEqual(self._settings().x_notification_mailbox_id, second)

    def test_domains_step_needs_a_domain(self):
        """The only way to finish step 4 is to name your domains."""
        self.assertTrue(self._settings().x_setup_domains_done)

        self.env['pan.mail.domain'].set_domains([])
        self.assertFalse(self._settings().x_setup_domains_done)

    def test_suggestion_button_fills_the_domain_field(self):
        self.env['pan.mail.mailbox'].create({
            'email': 'info@suggestme.test', 'mailbox_type': 'shared',
        })
        settings = self._settings()

        settings.action_apply_suggested_internal_domains()

        self.assertIn('suggestme.test', settings.x_internal_domain_ids.mapped('name'))

    def test_the_domains_line_shows_the_list(self):
        """The step shows its answer, not just its heading."""
        self.env['pan.mail.domain'].set_domains(['one.test', 'two.test'])

        settings = self._settings()

        self.assertEqual(settings.x_internal_domains_summary, 'one.test, two.test')
