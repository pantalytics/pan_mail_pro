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
        cls.Mailbox = cls.env['x_microsoft.mailbox']
        cls.MailServer = cls.env['ir.mail_server'].with_context(active_test=False)
        cls.placeholder = cls.env.ref('pan_mail_pro.mail_server_invalid_outlook_pro')
        cls.env['ir.config_parameter'].sudo().set_param(
            'x_pan_outlook_pro.smtp_takeover_done', 'False'
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
        self.Mailbox.create({'email': 'info@takeover.test', 'x_mailbox_type': 'shared'})

        self.assertFalse(self.customer_smtp.active,
                         "customer SMTP must be disabled once routing is live")
        self.assertTrue(self.placeholder.active)

    def test_takeover_runs_once(self):
        """A second mailbox must not re-disable a server an admin re-enabled."""
        self.Mailbox.create({'email': 'info@takeover.test', 'x_mailbox_type': 'shared'})
        self.customer_smtp.active = True

        self.Mailbox.create({'email': 'sales@takeover.test', 'x_mailbox_type': 'shared'})

        self.assertTrue(self.customer_smtp.active)


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestNotificationGapQueuesMail(TransactionCase):
    """The window between the first mailbox and a working notifications@."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.colleague = cls.env['res.users'].create({
            'name': 'Colleague', 'login': 'colleague@gap.test',
            'email': 'colleague@gap.test', 'notification_type': 'email',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])],
        })
        # Mail Pro is switched on (a mailbox exists) but notifications@ is not
        # configured yet — exactly the state an admin is in halfway through.
        cls.env['x_microsoft.mailbox'].create({
            'email': 'info@gap.test', 'x_mailbox_type': 'shared',
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
        cls.admin.sudo().write({
            'x_microsoft_refresh_token': 'fake-refresh',
            'x_microsoft_access_token': 'fake-access',
        })
        cls.env['pan.mail.internal.domains'].set_domains(['checklist.test'])

    def _settings(self, vals=None):
        return self.env['res.config.settings'].create(vals or {})

    def test_notification_mailbox_is_one_click(self):
        settings = self._settings({
            'x_setup_provider': 'outlook',
            'x_notification_mailbox_email': 'notifications@checklist.test',
        })

        settings.action_create_notification_mailbox()

        mailbox = self.env['x_microsoft.mailbox'].search([
            ('x_mailbox_type', '=', 'notification'),
        ])
        self.assertEqual(mailbox.email, 'notifications@checklist.test')
        self.assertEqual(mailbox.x_owner_user_id, self.admin,
                         "the admin who ran setup owns it — that is why step 3 "
                         "connects their account first")

    def test_notification_address_is_prefilled_from_the_domain(self):
        settings = self._settings()
        self.assertEqual(settings.x_notification_mailbox_email,
                         'notifications@checklist.test')

    def test_refuses_a_second_notification_mailbox(self):
        self._settings({
            'x_notification_mailbox_email': 'notifications@checklist.test',
        }).action_create_notification_mailbox()

        with self.assertRaises(UserError):
            self._settings({
                'x_notification_mailbox_email': 'other@checklist.test',
            }).action_create_notification_mailbox()

    def test_domains_step_accepts_either_answer(self):
        """Configured domains and a deliberate opt-out both complete the step."""
        self.assertTrue(self._settings().x_setup_domains_done)

        self.env['pan.mail.internal.domains'].set_domains([])
        self.assertFalse(self._settings().x_setup_domains_done)

        self.assertTrue(self._settings({'x_sync_internal_email': True}).x_setup_domains_done)

    def test_suggestion_button_fills_the_domain_field(self):
        self.env['x_microsoft.mailbox'].create({
            'email': 'info@suggestme.test', 'x_mailbox_type': 'shared',
        })
        settings = self._settings()

        settings.action_apply_suggested_internal_domains()

        self.assertIn('suggestme.test', settings.x_internal_domains)

    def test_provider_is_inferred_for_existing_databases(self):
        """An existing install must not be asked to pick what it already uses."""
        self.env['ir.config_parameter'].sudo().search([
            ('key', '=', 'x_pan_outlook_pro.setup_provider'),
        ]).unlink()
        self.env['ir.config_parameter'].sudo().set_param(
            'x_pan_outlook_pro.client_id', 'some-azure-app'
        )

        self.assertEqual(self.env['res.config.settings'].default_get(
            ['x_setup_provider'])['x_setup_provider'], 'outlook')

    def test_uncovered_mailbox_domain_surfaces_in_settings(self):
        self.env['x_microsoft.mailbox'].create({
            'email': 'info@forgotten.test', 'x_mailbox_type': 'shared',
        })

        settings = self._settings()

        self.assertIn('forgotten.test', settings.x_internal_domains_uncovered)
