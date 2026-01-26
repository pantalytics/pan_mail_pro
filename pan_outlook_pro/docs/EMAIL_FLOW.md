# Odoo Mail Architecture - Email Flow Analysis

## Overview

Dit document beschrijft hoe Odoo bepaalt of en hoe een email wordt verstuurd.

## Key Models

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              mail.message                                   │
│  - Alle communicatie (notes, messages, emails)                              │
│  - Linked aan business record via model + res_id                            │
│  - notification_ids → mail.notification (per recipient)                     │
│  - mail_ids → mail.mail (voor email verzending)                            │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                    ┌───────────────┴───────────────┐
                    ▼                               ▼
┌───────────────────────────────────┐ ┌───────────────────────────────────────┐
│        mail.notification          │ │              mail.mail                 │
│  - Per recipient tracking         │ │  - Actual email to send               │
│  - notification_type: inbox/email │ │  - _inherits mail.message             │
│  - notification_status            │ │  - is_notification field              │
│  - res_partner_id (recipient)     │ │  - email_to, body_html, etc           │
│  - mail_mail_id (optional link)   │ │                                       │
└───────────────────────────────────┘ └───────────────────────────────────────┘
```

## De Beslisboom: Inbox vs Email

De belangrijkste vraag: **Krijgt een recipient een Odoo inbox notificatie of een email?**

Dit wordt bepaald door `res.users.notification_type`:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    res.users.notification_type                          │
│                                                                         │
│   'inbox'  → Notificaties verschijnen in Odoo Inbox (GEEN email)       │
│   'email'  → Notificaties worden per email verstuurd                   │
│                                                                         │
│   Default: 'email'                                                      │
│   Locatie: User Preferences → Notification                              │
└─────────────────────────────────────────────────────────────────────────┘
```

## Complete Flow: Van Actie naar Email

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  USER ACTION                                                                │
│  - Post message in chatter ("Send message")                                 │
│  - @mention someone                                                         │
│  - Assign activity                                                          │
│  - System notification (auto)                                               │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  mail.thread.message_post()                                                 │
│  - Creates mail.message                                                     │
│  - Calls _notify_thread()                                                   │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  _notify_thread()                                                           │
│  1. _notify_get_recipients() → bepaalt WIE notificatie krijgt               │
│  2. _notify_thread_by_inbox() → voor users met notification_type='inbox'    │
│  3. _notify_thread_by_email() → voor users met notification_type='email'    │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                    ┌───────────────┴───────────────┐
                    ▼                               ▼
┌───────────────────────────────────┐ ┌───────────────────────────────────────┐
│   _notify_thread_by_inbox()       │ │    _notify_thread_by_email()          │
│                                   │ │                                       │
│   - Creates mail.notification     │ │   - Creates mail.mail record         │
│     with notification_type=inbox  │ │   - Creates mail.notification         │
│   - Sends bus notification        │ │     with notification_type=email     │
│   - NO email sent                 │ │   - Links notification.mail_mail_id   │
│                                   │ │   - Calls mail.send()                 │
└───────────────────────────────────┘ └───────────────────────────────────────┘
                                                    │
                                                    ▼
                                    ┌───────────────────────────────────────┐
                                    │         mail.mail.send()              │
                                    │   - OUR OVERRIDE IN pan_outlook_pro   │
                                    │   - Routes to Graph API               │
                                    └───────────────────────────────────────┘
```

## Wanneer wordt mail.mail aangemaakt?

**mail.mail wordt ALLEEN aangemaakt als:**
1. De recipient een `res.users` is met `notification_type='email'`
2. OF de recipient een externe partner is (geen user)
3. OF het een expliciete "Send message" naar externe email is

**mail.mail wordt NIET aangemaakt als:**
1. De recipient een `res.users` is met `notification_type='inbox'`
2. Het een interne note is (subtype met internal=True)

## De _notify_get_recipients() Query

De kritieke SQL in `mail_followers._get_recipient_data()`:

```sql
COALESCE(sub_user.notification_type, 'email') as notif
```

Dit betekent:
- Als recipient een USER is → gebruik user's notification_type
- Als recipient GEEN user is (externe partner) → default 'email'

## Jouw Use Case: Notification Mailbox

**Gewenst gedrag:**
- Emails naar **externe partners** → user's default mailbox (team1@)
- Emails naar **interne users** (notification) → notification mailbox (notifications@)

**Het onderscheid:**

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  CRITERIA: Is dit een "interne user notification"?                          │
│                                                                             │
│  JA als:                                                                    │
│  - mail.notification.notification_type = 'email'                            │
│  - mail.notification.res_partner_id.user_ids bestaat (partner is user)      │
│                                                                             │
│  Dit zijn emails die de user NIET zou krijgen als notification_type='inbox' │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Eenvoudige Implementatie

In plaats van complexe logica in `_is_internal_user_notification()`, kunnen we simpeler:

**Check of recipient_partner een interne user is:**

```python
def _get_mailbox_and_user(self):
    # Check recipients via recipient_ids field
    for partner in self.recipient_ids:
        if partner.user_ids:  # Partner is linked to a user
            # This is an internal user notification
            return self._get_notification_mailbox_and_user()

    # External partner email - use author's mailbox
    return self._get_author_mailbox_and_user()
```

## Samenvatting

| Scenario | Recipient | User notification_type | mail.mail created? | Mailbox to use |
|----------|-----------|----------------------|-------------------|----------------|
| @mention internal user | User partner | 'email' | ✅ Yes | notifications@ |
| @mention internal user | User partner | 'inbox' | ❌ No | N/A (no email) |
| Send message to customer | External partner | N/A | ✅ Yes | team1@ |
| Activity reminder | User partner | 'email' | ✅ Yes | notifications@ |
| Activity reminder | User partner | 'inbox' | ❌ No | N/A (no email) |

## Sources

- [Odoo Forum: mail.message vs mail.mail](https://www.odoo.com/forum/help-1/what-is-the-difference-between-mailmessage-and-mailmail-7390)
- [Odoo Forum: Notification Types](https://www.odoo.com/forum/help-1/how-does-notifications-inbox-tome-and-todo-mailboxes-work-69)
- Odoo 19 source: `addons/mail/models/mail_thread.py`
- Odoo 19 source: `addons/mail/models/mail_followers.py`
- Odoo 19 source: `addons/mail/models/res_users.py`
