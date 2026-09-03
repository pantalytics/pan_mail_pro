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
from odoo.exceptions import UserError
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

    def test_queued_mail_is_visible_in_the_checklist(self):
        self._notification_to_colleague().send()

        settings = self.env['res.config.settings'].create({})

        self.assertEqual(settings.x_setup_pending_notifications, 1)

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

    def test_notification_mailbox_is_one_click(self):
        settings = self._settings({
            'x_mail_provider': 'outlook',
            'x_notification_mailbox_email': 'notifications@checklist.test',
        })

        settings.action_create_notification_mailbox()

        mailbox = self.env['pan.mail.mailbox'].search([
            ('is_notification_mailbox', '=', True),
        ])
        self.assertEqual(mailbox.email, 'notifications@checklist.test')
        self.assertEqual(mailbox.owner_user_id, self.admin,
                         "the admin who ran setup owns it — that is why step 3 "
                         "connects their account first")
        self.assertEqual(mailbox.provider, 'outlook',
                         "the mailbox is served by the provider being set up, "
                         "whichever one that is")

    def test_notification_address_is_prefilled_from_the_domain(self):
        settings = self._settings()
        self.assertEqual(settings.x_notification_mailbox_email,
                         'notifications@checklist.test')

    def test_refuses_a_second_notification_mailbox(self):
        self._settings({
            'x_mail_provider': 'outlook',
            'x_notification_mailbox_email': 'notifications@checklist.test',
        }).action_create_notification_mailbox()

        with self.assertRaises(UserError):
            self._settings({
                'x_mail_provider': 'outlook',
                'x_notification_mailbox_email': 'other@checklist.test',
            }).action_create_notification_mailbox()

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

    def test_notification_mailbox_needs_a_provider(self):
        """Step 1 gates step 5: without a provider there is nothing to serve it."""
        self.env['ir.config_parameter'].sudo().set_param(
            'pan_mail_pro.setup_provider', ''
        )
        with self.assertRaises(UserError):
            self._settings({
                'x_notification_mailbox_email': 'notifications@checklist.test',
            }).action_create_notification_mailbox()

    def test_uncovered_mailbox_domain_surfaces_in_settings(self):
        self.env['pan.mail.mailbox'].create({
            'email': 'info@forgotten.test', 'mailbox_type': 'shared',
        })

        settings = self._settings()

        self.assertIn('forgotten.test', settings.x_internal_domains_uncovered)
