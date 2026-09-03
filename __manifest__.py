# -*- coding: utf-8 -*-
{
    'name': "Mail Pro - Email Integration",
    'summary': "Microsoft 365, Gmail or IMAP/SMTP: send from any mailbox, sync incoming mail into the chatter, thread replies properly",
    'description': """
        Mail Pro - Secure Professional Email Integration
        ====================================================

        Full control over your email - Microsoft 365, Google Workspace or any
        IMAP/SMTP mailbox. Send from any mailbox, receive everything.

        Key Benefits:
        -------------
        - **Full sender control**: Choose exactly which mailbox to send from
        - **Secure**: OAuth 2.0 where the provider offers it, encrypted credential storage
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
        - Automatic sync from your mailboxes, whatever the provider
        - 2-way sync: both Inbox and Sent Items
        - Automatic threading via In-Reply-To headers
        - Auto-create contacts for unknown senders
        - Activity creation for team assignment

        **Providers:**
        - Microsoft 365 via the Graph API (OAuth 2.0)
        - Google Workspace via the Gmail API (OAuth 2.0)
        - Any IMAP/SMTP mailbox (Soverin, Fastmail, your own server) via
          server, login and password

        **Security:**
        - OAuth 2.0 authentication where the provider offers it
        - Encrypted credential storage using Fernet encryption
        - Single-tenant Azure App Registration for Microsoft 365

        How to Use:
        -----------
        **Sending:**
        1. For quick messages: Use the inline Chatter composer (uses your default mailbox)
        2. To select a specific mailbox: Click the full screen icon in Chatter
        3. Set your default mailbox in: Settings → Users → Your User → Email tab

        **Receiving:**
        1. Go to Settings → Mail Pro → Manage Mailbox List
        2. Open a mailbox and go to the "Incoming Sync" tab
        3. Enable sync and select the user whose account reads the mailbox
        4. Emails will appear in the partner's chatter automatically

        Why this module?
        ----------------
        Built-in email options have limitations:

        1. Standard SMTP: Requires insecure DNS settings
        2. Microsoft Outlook App: No control over sender - always "notifications@..."
        3. Fetchmail: Complex setup, no Graph API support

        This module gives you full control through the provider's own API.

        Documentation:
        --------------
        Full setup guide: https://pantalytics.gitbook.io/pantalytics-docs/

        Setup Instructions:
        -------------------
        After installation, go to Settings → Mail Pro
        Follow the step-by-step guide for your provider.

        API Permissions needed in Azure for Microsoft 365 (delegated; the same ones the setup
        guide lists and the OAuth request asks for, because a permission
        granted in the portal but absent from the request is not in the token):
        - User.Read, offline_access (authentication and refresh tokens)
        - Mail.ReadWrite, Mail.ReadWrite.Shared (create the draft, read mail)
        - Mail.Send, Mail.Send.Shared (send the draft)

        Data Disclosure:
        ----------------
        - Email content is sent/received via the Microsoft Graph API, the
          Gmail API or your own IMAP/SMTP server, depending on which provider
          a mailbox uses.
        - OAuth tokens and mailbox passwords are stored encrypted in your Odoo
          database.
        - No data is sent to Pantalytics, the module author.
        - AI triage is OFF by default and OFF per mailbox. If you enable it,
          you supply your own AI provider API key and your Odoo talks to that
          provider directly; Pantalytics does not proxy, see, or store any of
          it. Only an email's envelope is sent - subject, sender, recipient,
          date, and a shortlist of candidate record names. Message bodies and
          attachments are never sent to an AI provider.
        - You are the data controller for anything you send to an AI provider,
          and that provider is your processor. Check your agreement with them
          before enabling this.
    """,
    'author': "Pantalytics B.V. by Rutger Hofste",
    'website': "https://www.pantalytics.com/apps/mail-pro/",
    'support': "support@pantalytics.com",
    'category': 'Discuss',
    'version': '19.0.6.4.1',
    'license': 'LGPL-3',
    'depends': ['mail', 'base', 'crm'],
    'external_dependencies': {
        'python': ['cryptography'],
    },
    'data': [
        'security/pan_mail_pro_security.xml',
        'security/ir.model.access.csv',
        'data/ir_cron_data.xml',
        'data/mail_server_data.xml',
        'data/mail_template_data.xml',
        'views/pan_mail_mailbox_views.xml',
        'views/pan_mail_routing_log_views.xml',
        'views/pan_mail_account_views.xml',
        'views/mail_message_views.xml',
        'views/pan_mail_domain_views.xml',
        'views/pan_mail_coverage_views.xml',
        'views/pan_mail_item_views.xml',
        'views/templates/oauth_templates.xml',
        'views/res_config_settings_views.xml',
        'views/res_users_views.xml',
        'views/res_partner_views.xml',
        'views/mail_compose_message_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'pan_mail_pro/static/src/scss/setup_status.scss',
            'pan_mail_pro/static/src/js/mailbox_list_controller.js',
            'pan_mail_pro/static/src/xml/mailbox_list_view.xml',
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
