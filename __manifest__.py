# -*- coding: utf-8 -*-
{
    'name': "Mail Pro - Email Integration",
    'summary': "Your own mailbox in Odoo: Microsoft 365, Gmail or IMAP/SMTP",
    'description': """
        Mail Pro - Secure Professional Email Integration
        ====================================================

        Full control over your email - Microsoft 365, Google Workspace or any
        IMAP/SMTP mailbox. Send from your own address, and read back as much
        of the mailbox as you choose.

        Key Benefits:
        -------------
        - **Full sender control**: Choose exactly which mailbox to send from
        - **Secure**: OAuth 2.0 where the provider offers it, encrypted credential storage
        - **Replies land**: An answer to a mail Odoo sent lands on its record, with no setting
        - **Proper addresses**: No confusing "notifications@..." or reply-to aliases

        Features:
        ---------
        **Outgoing Email:**
        - 'Send From' dropdown in email composer (like Outlook)
        - Send from personal, shared, or group mailboxes
        - Default mailbox configuration per user
        - Correct From and Reply-To headers

        **Incoming Email:**
        - Automatic sync from your mailboxes, every minute, whatever the provider
        - One sync level per mailbox, each keeping strictly more than the one above:
          replies only (default); also your own replies from the Sent folder;
          also new email from existing contacts; also new email from everyone,
          who then become contacts
        - Threading by our own headers, then the References chain, then the
          provider's thread id
        - Route new conversations to a team alias, so they create a lead or a ticket
        - Internal email, between your own domains, is never synced

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
        3. Set your default mailbox in: Settings → Users → Your User → Mail Pro tab

        **Receiving:**
        1. Replies to mail Odoo sent land on their record with no setting at all
        2. For more, go to Settings → Technical → Email → Mail Pro → Mailboxes,
           open a mailbox and pick a Sync level on the Sync Settings tab
        3. New email lands on the sender's contact, or on the team you route it to

        Why this module?
        ----------------
        Built-in email options have limitations:

        1. Standard SMTP: Requires insecure DNS settings
        2. Microsoft Outlook App: No control over sender - always "notifications@..."
        3. Fetchmail: Complex setup, no Graph API support

        This module gives you full control through the provider's own API.

        Documentation:
        --------------
        Full setup guide: https://odoo.pantalytics.com/knowledge/article/116

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
        - MailboxSettings.Read, User.ReadBasic.All: optional, asked for only
          when the system parameter pan_mail_pro.graph_inspect_scopes is set
          (tell a user account from a shared mailbox and an alias; the
          access check itself needs nothing beyond the permissions above)

        Data Disclosure:
        ----------------
        - Email content is sent/received via the Microsoft Graph API, the
          Gmail API or your own IMAP/SMTP server, depending on which provider
          a mailbox uses.
        - OAuth tokens and mailbox passwords are stored encrypted in your Odoo
          database.
        - Nothing is sent to Pantalytics unless an administrator links the
          database under Settings -> Mail Pro -> Pantalytics Account. Once
          linked, the database reports once a day: its database id, the
          module and Odoo version, how many accounts are connected, whether
          sync is healthy, which of the three setup steps are answered, and
          for the last 24 hours how many mails were
          sent and received, how many were
          linked to a document, to a contact only, or to nothing, per
          matching rule how often it decided and how often a person
          overruled it, how many conversations were linked by hand, and
          which kinds of failure happened how often (a fixed list of error
          codes such as "outgoing.send_failed", with a count each).
          Counts, rule names and error codes. Never an address, subject,
          body, name, exception text or traceback.
        - Help improve Mail Pro is a switch on the Pantalytics workspace, off
          until an administrator turns it on there. When it is on, the Mail
          Pro inbox in the browser reports which screens, tabs and buttons
          are used and records the session with every word and every field
          masked, to Pantalytics only, never to a third party directly. An
          Odoo administrator can refuse it for this instance under Settings
          -> Mail Pro -> Pantalytics Account.
        - No AI provider is contacted. The module has no AI feature and ships
          no AI vendor SDK.
    """,
    'author': "Pantalytics B.V. by Rutger Hofste",
    'website': "https://www.pantalytics.com/apps/mail-pro/",
    'support': "support@pantalytics.com",
    'category': 'Discuss',
    'version': '19.0.28.1.2',
    'license': 'Other proprietary',  # Elastic License 2.0 — see LICENSE
    'depends': ['mail', 'base', 'crm'],
    'external_dependencies': {
        'python': ['cryptography', 'requests'],
    },
    'data': [
        'security/pan_mail_pro_security.xml',
        'security/ir.model.access.csv',
        'data/ir_cron_data.xml',
        'data/mail_server_data.xml',
        'data/mail_template_data.xml',
        'views/pan_mail_menus.xml',
        'views/pan_mail_conversation_views.xml',
        'views/pan_mail_mailbox_views.xml',
        'views/pan_mail_mailbox_access_views.xml',
        'views/pan_mail_routing_log_views.xml',
        'views/pan_mail_error_views.xml',
        'views/pan_mail_provider_views.xml',
        'views/pan_mail_license_views.xml',
        'views/pan_mail_account_views.xml',
        'views/mail_message_views.xml',
        'views/pan_mail_domain_views.xml',
        'views/pan_mail_coverage_views.xml',
        'views/templates/oauth_templates.xml',
        'views/res_config_settings_views.xml',
        'views/res_users_views.xml',
        'views/res_partner_views.xml',
        'views/mail_compose_message_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'pan_mail_pro/static/src/scss/setup_status.scss',
            'pan_mail_pro/static/src/scss/conversation_view.scss',
            'pan_mail_pro/static/src/scss/connect_banner.scss',
            'pan_mail_pro/static/src/js/mailbox_list_controller.js',
            'pan_mail_pro/static/src/js/provider_form.js',
            'pan_mail_pro/static/src/js/connect_banner.js',
            'pan_mail_pro/static/src/js/chatter_door.js',
            'pan_mail_pro/static/src/js/improve.js',
            'pan_mail_pro/static/src/js/conversation_view/use_panes.js',
            'pan_mail_pro/static/src/js/conversation_view/use_composer.js',
            'pan_mail_pro/static/src/js/conversation_view/link_dialog.js',
            'pan_mail_pro/static/src/js/conversation_view/conversation_view.js',
            'pan_mail_pro/static/src/xml/mailbox_list_view.xml',
            'pan_mail_pro/static/src/xml/provider_form.xml',
            'pan_mail_pro/static/src/xml/connect_banner.xml',
            'pan_mail_pro/static/src/xml/chatter_door.xml',
            'pan_mail_pro/static/src/xml/conversation_view.xml',
            'pan_mail_pro/static/src/xml/link_dialog.xml',
        ],
        # Help improve Mail Pro: posthog-js (static/lib/posthog, MIT), in a
        # bundle of its own that improve.js loads only on an opted-in
        # instance, so a screen that never records never carries 650KB of SDK.
        'pan_mail_pro.assets_improve': [
            'pan_mail_pro/static/lib/posthog/posthog.js',
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
    'uninstall_hook': '_restore_smtp_servers',
}
