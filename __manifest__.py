# -*- coding: utf-8 -*-
{
    'name': "Outlook Pro - Microsoft 365 Email Integration",
    'summary': "Send from shared mailboxes, 2-way sync, proper threading - via Graph API",
    'description': """
        Outlook Pro - Secure Professional Email Integration
        ====================================================

        Full control over your Microsoft 365 email - send from any mailbox, receive everything.

        Key Benefits:
        -------------
        - **Full sender control**: Choose exactly which mailbox to send from
        - **Secure**: OAuth 2.0 + encrypted token storage, no insecure DNS hacks
        - **2-way sync**: Incoming and outgoing emails synced to Odoo chatter
        - **Proper addresses**: No confusing "notifications@..." or reply-to aliases

        Features:
        ---------
        **Outgoing Email:**
        - 'Send From' dropdown in email composer (like Outlook)
        - Send from personal, shared, or group mailboxes
        - Default mailbox configuration per user
        - Correct From and Reply-To headers

        **Incoming Email:**
        - Automatic sync from Microsoft 365 mailboxes
        - 2-way sync: both Inbox and Sent Items
        - Automatic threading via In-Reply-To headers
        - Auto-create contacts for unknown senders
        - Activity creation for team assignment

        **Security:**
        - OAuth 2.0 authentication (no passwords stored)
        - Encrypted token storage using Fernet encryption
        - Single-tenant Azure App Registration

        How to Use:
        -----------
        **Sending:**
        1. For quick messages: Use the inline Chatter composer (uses your default mailbox)
        2. To select a specific mailbox: Click the full screen icon in Chatter
        3. Set your default mailbox in: Settings → Users → Your User → Email tab

        **Receiving:**
        1. Go to Settings → Outlook Pro → Manage Mailbox List
        2. Open a mailbox and go to the "Incoming Sync" tab
        3. Enable sync and select a user with Microsoft connection
        4. Emails will appear in the partner's chatter automatically

        Why this module?
        ----------------
        Built-in email options have limitations:

        1. Standard SMTP: Requires insecure DNS settings
        2. Microsoft Outlook App: No control over sender - always "notifications@..."
        3. Fetchmail: Complex setup, no Graph API support

        This module gives you full control via Microsoft Graph API.

        Documentation:
        --------------
        Full setup guide: https://pantalytics.gitbook.io/pantalytics-docs/

        Setup Instructions:
        -------------------
        After installation, go to Settings → Outlook Pro
        Follow the step-by-step Azure configuration guide.

        API Permissions needed:
        - User.Read (for authentication)
        - Mail.Send, Mail.Send.Shared (for sending)
        - Mail.Read, Mail.Read.Shared (for receiving)

        Data Disclosure:
        ----------------
        - Email content is sent/received via Microsoft Graph API
        - OAuth tokens are stored encrypted in your Odoo database
        - No data is sent to the module author or any third party
    """,
    'author': "Pantalytics B.V. by Rutger Hofste",
    'website': "https://www.pantalytics.com/apps/outlook-pro/",
    'support': "support@pantalytics.com",
    'category': 'Discuss',
    'version': '19.0.1.0.20',
    'license': 'LGPL-3',
    'depends': ['mail', 'base', 'crm'],
    'external_dependencies': {
        'python': ['cryptography'],
    },
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron_data.xml',
        'data/mail_server_data.xml',
        'views/microsoft_mailbox_views.xml',
        'wizard/microsoft_oauth_wizard_views.xml',
        'views/templates/oauth_templates.xml',
        'views/res_config_settings_views.xml',
        'views/res_users_views.xml',
        'views/res_partner_views.xml',
        'views/mail_compose_message_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'pan_outlook_pro/static/src/js/mailbox_list_controller.js',
            'pan_outlook_pro/static/src/xml/mailbox_list_view.xml',
        ],
    },
    'images': [
        'static/description/banner.png',
        'static/description/composer_screenshot.png',
        'static/description/settings_screenshot.png',
        'static/description/sync_screenshot.png',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
    'post_init_hook': '_disable_smtp_servers',
}
