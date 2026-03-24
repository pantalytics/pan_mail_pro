# -*- coding: utf-8 -*-
"""
Unit tests for Microsoft Incoming Mail Processor.

Run with: python -m odoo -d test_db --test-enable --test-tags=pan_outlook_pro
"""
from odoo.tests import TransactionCase, tagged
import unittest



@tagged('pan_outlook_pro', 'post_install', '-at_install')
class TestInternalDomain(TransactionCase):
    """Test internal domain filtering logic using Odoo's mail.alias.domain."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.processor = cls.env['microsoft.incoming.mail.processor']
        # Create alias domains (Odoo's standard way to configure internal domains)
        cls.alias_domain_1 = cls.env['mail.alias.domain'].create({
            'name': 'company.com',
        })
        cls.alias_domain_2 = cls.env['mail.alias.domain'].create({
            'name': 'internal.org',
        })

    def test_internal_domain_match(self):
        """Emails from alias domains should be detected as internal."""
        self.assertTrue(self.processor._is_internal_domain('user@company.com'))
        self.assertTrue(self.processor._is_internal_domain('user@internal.org'))

    def test_internal_domain_case_insensitive(self):
        """Domain matching should be case insensitive."""
        self.assertTrue(self.processor._is_internal_domain('user@COMPANY.COM'))
        self.assertTrue(self.processor._is_internal_domain('user@Company.Com'))

    def test_external_domain(self):
        """Emails from external domains should not be detected as internal."""
        self.assertFalse(self.processor._is_internal_domain('user@gmail.com'))
        self.assertFalse(self.processor._is_internal_domain('user@external.com'))

    def test_invalid_email(self):
        """Invalid emails should return False."""
        self.assertFalse(self.processor._is_internal_domain(''))
        self.assertFalse(self.processor._is_internal_domain('invalid'))
        self.assertFalse(self.processor._is_internal_domain(None))

    def test_no_matching_alias_domain(self):
        """When email domain doesn't match any alias domain, it's not internal."""
        # external.net is not in our configured alias domains
        self.assertFalse(self.processor._is_internal_domain('user@external.net'))

    def test_per_mailbox_exclude_internal_enabled(self):
        """With per-mailbox exclude_internal enabled, internal emails are skipped."""
        mailbox = self.env['x_microsoft.mailbox'].create({
            'email': 'support@company.com',
            'x_mailbox_type': 'shared',
            'x_exclude_internal': True,  # Default: exclude internal
        })
        # Internal email should be skipped
        self.assertTrue(self.processor._is_internal_domain('user@company.com', mailbox))

    def test_per_mailbox_exclude_internal_disabled(self):
        """With per-mailbox exclude_internal disabled, internal emails are included."""
        mailbox = self.env['x_microsoft.mailbox'].create({
            'email': 'team@company.com',
            'x_mailbox_type': 'shared',
            'x_exclude_internal': False,  # Include internal emails for this mailbox
        })
        # Internal email should NOT be skipped for this mailbox
        self.assertFalse(self.processor._is_internal_domain('user@company.com', mailbox))


@tagged('pan_outlook_pro', 'post_install', '-at_install')
class TestDuplicateDetection(TransactionCase):
    """Test duplicate message detection."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.processor = cls.env['microsoft.incoming.mail.processor']
        cls.partner = cls.env['res.partner'].create({
            'name': 'Test Partner',
            'email': 'test@example.com',
        })

    def test_no_duplicate_for_new_message(self):
        """New message IDs should not be detected as duplicates."""
        self.assertFalse(self.processor._is_duplicate('<new-message-id@example.com>'))

    def test_duplicate_in_mail_message(self):
        """Messages already in mail.message should be detected."""
        # Create a mail.message with this message_id
        self.env['mail.message'].create({
            'message_id': '<existing-message@example.com>',
            'model': 'res.partner',
            'res_id': self.partner.id,
            'body': 'Test',
        })
        self.assertTrue(self.processor._is_duplicate('<existing-message@example.com>'))

    def test_duplicate_in_mail_mail(self):
        """Messages sent via our module should be detected as duplicates."""
        # Create a mail.mail with Microsoft message ID
        self.env['mail.mail'].create({
            'subject': 'Test',
            'body_html': '<p>Test</p>',
            'x_microsoft_message_id': '<sent-via-graph@outlook.com>',
        })
        self.assertTrue(self.processor._is_duplicate('<sent-via-graph@outlook.com>'))

    def test_empty_message_id(self):
        """Empty message IDs should not be duplicates."""
        self.assertFalse(self.processor._is_duplicate(''))
        self.assertFalse(self.processor._is_duplicate(None))


@tagged('pan_outlook_pro', 'post_install', '-at_install')
class TestPartnerMatching(TransactionCase):
    """Test partner finding and creation."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.processor = cls.env['microsoft.incoming.mail.processor']
        cls.existing_partner = cls.env['res.partner'].create({
            'name': 'Existing Partner',
            'email': 'existing@example.com',
        })

    def test_find_existing_partner(self):
        """Should find existing partner by email."""
        partner = self.processor._find_partner('existing@example.com')
        self.assertEqual(partner, self.existing_partner)

    def test_find_partner_case_insensitive(self):
        """Partner lookup should be case insensitive."""
        partner = self.processor._find_partner('EXISTING@EXAMPLE.COM')
        self.assertEqual(partner, self.existing_partner)

    def test_find_partner_not_found(self):
        """Should return False for unknown emails."""
        partner = self.processor._find_partner('unknown@example.com')
        self.assertFalse(partner)

    def test_find_or_create_existing(self):
        """Should find existing partner without creating new one."""
        partner = self.processor._find_or_create_partner('existing@example.com', 'Some Name')
        self.assertEqual(partner, self.existing_partner)
        # Name should not be updated
        self.assertEqual(partner.name, 'Existing Partner')

    def test_find_or_create_new(self):
        """Should create new partner for unknown email."""
        partner = self.processor._find_or_create_partner('new@example.com', 'New Person')
        self.assertTrue(partner)
        self.assertEqual(partner.name, 'New Person')
        self.assertEqual(partner.email, 'new@example.com')

    def test_find_or_create_no_name(self):
        """Should use email local part as name if not provided."""
        partner = self.processor._find_or_create_partner('noname@example.com')
        self.assertEqual(partner.name, 'noname')


@tagged('pan_outlook_pro', 'post_install', '-at_install')
class TestAliasRouting(TransactionCase):
    """Test email routing via aliases."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if 'helpdesk.team' not in cls.env:
            raise unittest.SkipTest("Helpdesk module not installed")
        cls.processor = cls.env['microsoft.incoming.mail.processor']
        cls.partner = cls.env['res.partner'].create({
            'name': 'Test Sender',
            'email': 'sender@example.com',
        })

        # Create a helpdesk team and mailbox with alias + route_to_team enabled
        cls.helpdesk_team = cls.env['helpdesk.team'].create({
            'name': 'Test Support',
        })
        cls.mailbox = cls.env['x_microsoft.mailbox'].create({
            'email': 'support@company.com',
            'x_mailbox_type': 'shared',
            'x_route_to_team': True,  # Enable team routing
            'x_alias_id': cls.helpdesk_team.alias_id.id,
        })

    def test_route_to_helpdesk(self):
        """Email should be routed to helpdesk ticket via alias."""
        msg_dict = {
            'message_type': 'email',
            'subject': 'Help needed',
            'from': '"Test Sender" <sender@example.com>',
            'to': 'support@company.com',
            'body': '<p>I need help</p>',
            'attachments': [],
            'message_id': '<test-123@example.com>',
            'author_id': self.partner.id,
            'email_from': '"Test Sender" <sender@example.com>',
        }

        record, message = self.processor._route_email_via_alias(
            mailbox=self.mailbox,
            partner=self.partner,
            msg_dict=msg_dict,
            contact_email='sender@example.com',
        )

        self.assertEqual(record._name, 'helpdesk.ticket')
        self.assertEqual(record.name, 'Help needed')
        self.assertEqual(record.partner_id, self.partner)
        self.assertTrue(message)

    def test_route_no_alias_falls_back_to_partner(self):
        """Without alias, email should be posted to partner chatter."""
        mailbox_no_alias = self.env['x_microsoft.mailbox'].create({
            'email': 'noalias@company.com',
            'x_mailbox_type': 'shared',
        })

        msg_dict = {
            'message_type': 'email',
            'subject': 'Test',
            'body': '<p>Test</p>',
            'attachments': [],
            'message_id': '<test-456@example.com>',
            'author_id': self.partner.id,
            'email_from': 'sender@example.com',
        }

        record, message = self.processor._route_email_via_alias(
            mailbox=mailbox_no_alias,
            partner=self.partner,
            msg_dict=msg_dict,
            contact_email='sender@example.com',
        )

        self.assertEqual(record, self.partner)
        self.assertTrue(message)
