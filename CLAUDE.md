# Claude Project Settings

Project context for Claude Code / Claude AI assistants.

## Project Overview

**Odoo Customer Project: Goudsmit**

Custom Odoo 19 implementation with Microsoft 365 email integration via the `pan_outlook_pro` addon.

## Key Module: pan_outlook_pro

Location: `addons/pan_outlook_pro/` (git submodule → `git@github.com:pantalytics/odoo-outlook-pro.git`)

### Purpose
Complete Microsoft 365 email integration - send and receive emails via Microsoft Graph API with proper threading and partner management.

### Architecture

```
pan_outlook_pro/
├── models/
│   ├── mail_mail.py              # Outgoing email override (Graph API send)
│   ├── mail_compose_message.py   # Composer "Send From" dropdown
│   ├── microsoft_mailbox.py      # Mailbox configuration (x_microsoft.mailbox)
│   ├── microsoft_incoming_mail.py # Incoming email processor (cron)
│   ├── microsoft_graph_client.py  # Graph API client (all API calls)
│   ├── res_users.py              # User OAuth tokens
│   ├── res_config_settings.py    # Module settings (client_id, secret, tenant)
│   └── encryption_utils.py       # Fernet encryption for tokens
├── controllers/
│   └── microsoft_oauth.py        # OAuth callback handler
├── wizard/
│   └── microsoft_oauth_wizard.py # Connect Microsoft account wizard
├── views/
├── data/
│   └── ir_cron_data.xml          # Incoming mail cron (1 min interval)
└── security/
```

### Key Technical Decisions

1. **Polling over Webhooks** - Works on Odoo.sh without public endpoints
2. **Native mail.thread.message_process()** - Uses Odoo's routing instead of custom inbox
3. **Microsoft Message-ID fetching** - After send, fetch ID from Sent Items for reply threading
4. **Pre-create partners** - Create partner before message_process to ensure correct name
5. **First sync skips history** - Sets timestamp, imports nothing on first run
6. **Stored computed field** - `x_microsoft_oauth_connected` for domain filtering

### Field Naming Convention
All custom fields use `x_` prefix per Odoo.sh guidelines.

## Development Environment

### Local Docker Setup
```bash
cd .local
docker-compose up -d
# Odoo at http://localhost:8069
# Database: test_db (auto-created)
```

### Useful Commands
```bash
# Restart Odoo (applies Python changes)
docker-compose restart odoo

# View logs
docker-compose logs -f odoo

# Full rebuild (loses data)
docker-compose down -v && docker-compose up -d
```

### Submodule Management (pan_outlook_pro)
```bash
# Update submodule to latest version
git submodule update --remote addons/pan_outlook_pro

# Commit the updated reference
git add addons/pan_outlook_pro
git commit -m "Update pan_outlook_pro submodule"
git push

# After git pull: sync submodule to correct commit
git submodule update --init
```

Note: Submodules point to a specific commit, not a branch. Odoo.sh uses the commit referenced in this repo.

### Testing Email Sync
1. Configure mailbox in Settings → Outlook Pro → Manage Mailbox List
2. Enable incoming sync with a user that has Microsoft OAuth
3. Check logs for `[Incoming Mail]` and `[Graph API]` entries
4. Cron runs every 1 minute, or trigger manually via Scheduled Actions

## Important Files

- [README.md](addons/pan_outlook_pro/README.md) - Setup instructions
- [DESIGN_DECISIONS.md](addons/pan_outlook_pro/DESIGN_DECISIONS.md) - Architecture rationale
- [__manifest__.py](addons/pan_outlook_pro/__manifest__.py) - Module metadata

## Common Tasks

### Adding a new Graph API method
1. Add method to `microsoft_graph_client.py`
2. Use `get_valid_token(user)` for authentication
3. Handle errors with `_extract_graph_error()`

### Debugging email issues
1. Check Odoo logs for `[Graph API]` and `[Incoming Mail]` tags
2. Verify OAuth tokens: user should have `x_microsoft_oauth_connected = True`
3. Check mailbox state: should be 'active' with `x_incoming_user_id` set

### Odoo 19 Compatibility
- No `numbercall` on cron jobs (deprecated)
- Use `invisible` instead of `attrs` in views
- Stored computed fields need `@api.depends` decorator
