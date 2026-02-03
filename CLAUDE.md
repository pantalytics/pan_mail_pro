# Claude Code Context

Project context for Claude Code AI assistant.

## Module Overview

**pan_outlook_pro** - Microsoft 365 email integration for Odoo 19.0 Enterprise Edition.

Send and receive emails via Microsoft Graph API with OAuth 2.0 delegated permissions.

## Development Principles

1. **Odoo 19 Compatibility** - Before every commit, verify code meets Odoo 19 requirements
2. **Minimal Footprint** - Stay as close to standard Odoo as possible
3. **Code Discipline** - Push back on feature requests that cause unnecessary bloat
4. **Lean Implementation** - Prefer configuration over code, reuse Odoo native features

### Odoo 19 Checklist (verify before commit)
- [ ] No `attrs` in views → use `invisible`, `readonly`, `required` directly
- [ ] No `numbercall` on cron jobs (deprecated)
- [ ] Stored computed fields have `@api.depends` decorator
- [ ] Use `groups` attribute for field access control
- [ ] XML ids follow pattern: `module_name.record_name`

## Key Files

| File | Purpose |
|------|---------|
| `models/mail_mail.py` | Outgoing email override (Graph API send) |
| `models/mail_message.py` | Microsoft message ID storage for threading |
| `models/mail_compose_message.py` | Composer "Send From" dropdown + setup warning |
| `models/mail_alias.py` | Cleaner alias display (name only, no domain) |
| `models/microsoft_mailbox.py` | Mailbox configuration + routing rules |
| `models/microsoft_graph_client.py` | All Graph API calls |
| `models/microsoft_incoming_mail.py` | Incoming email sync (uses `message_new()`) |
| `models/res_partner.py` | Contact block list field |
| `controllers/main.py` | OAuth callback handler |
| `tests/test_incoming_mail.py` | Unit tests for incoming mail processor |

## Mailbox Types

| Type | Who sees it? | OAuth token used |
|------|--------------|------------------|
| Personal | Only owner | Owner's token |
| Shared | Everyone | Sender's own token |
| Notification | Everyone | Owner's token |

## Development

### Local Docker Setup
```bash
cd .local
docker-compose up -d
# Odoo at http://localhost:8069
# Database: test_db
```

### Restart after Python changes
```bash
docker-compose restart odoo
```

### Upgrade module (apply model changes)
```bash
docker-compose exec -T odoo python -m odoo -c /etc/odoo/odoo.conf -d test_db -u pan_outlook_pro --stop-after-init
docker-compose restart odoo
```

### View logs
```bash
docker-compose logs -f odoo
```

### Run unit tests
```bash
docker-compose stop odoo
docker-compose run --rm odoo python -m odoo -c /etc/odoo/odoo.conf \
  -d test_db -u pan_outlook_pro --test-enable --test-tags=pan_outlook_pro --stop-after-init
docker-compose start odoo
```

## Conventions

- All custom fields use `x_` prefix (Odoo.sh requirement)
- Log tags: `[Graph API]`, `[Incoming Mail]`, `[OAuth]`
- Use `invisible` instead of `attrs` in views (Odoo 19)
- Stored computed fields need `@api.depends` decorator

## Common Tasks

### Adding a new Graph API method
1. Add method to `microsoft_graph_client.py`
2. Use `get_valid_token(user)` for authentication
3. Handle errors with `_extract_graph_error()`

### Debugging email issues
1. Check Odoo logs for `[Graph API]` and `[Incoming Mail]` tags
2. Verify OAuth: user should have `x_microsoft_oauth_connected = True`
3. Check mailbox state: should be 'active'

## Key Design Decisions

### Use `message_new()` for incoming emails
**Critical:** For new incoming emails, use Odoo's native `message_new()` instead of manual record creation + `message_post()`.

```python
# CORRECT - uses Odoo's native flow
record = Model.message_new(msg_dict, custom_values=custom_values)

# WRONG - triggers unwanted notifications
record = Model.create(vals)
record.message_post(body=body, ...)  # Sends follower notifications!
```

**Why:** `message_new()` is what Odoo's standard mail gateway uses. It:
- Creates the record correctly
- Posts the initial message
- Triggers auto-replies (Helpdesk acknowledgment)
- Does NOT send duplicate notifications to sender

### Notification mailbox required for incoming sync
When enabling incoming sync on any mailbox, a Notification mailbox must exist. This handles:
- Emails from external authors (no Odoo user)
- System notifications triggered by incoming mail

## OWL Frontend (Odoo 17+)

Custom list view buttons require JavaScript. Pattern:

1. **Controller** (`static/src/js/my_list_controller.js`):
```javascript
/** @odoo-module */
import { ListController } from "@web/views/list/list_controller";
import { listView } from "@web/views/list/list_view";
import { registry } from "@web/core/registry";

export class MyListController extends ListController {
    onCustomAction() {
        this.actionService.doAction({ type: "ir.actions.act_url", url: "/my/url" });
    }
}

export const myListView = { ...listView, Controller: MyListController, buttonTemplate: "my_module.MyListButtons" };
registry.category("views").add("my_list", myListView);
```

2. **Template** (`static/src/xml/my_list_view.xml`):
```xml
<t t-name="my_module.MyListButtons">
    <button class="btn btn-secondary" t-on-click="() => this.onCustomAction()">Button</button>
</t>
```

3. **Manifest**: Add to `web.assets_backend`
4. **View**: Use `js_class="my_list"`

## Documentation

- [README.md](README.md) - Setup instructions for users
- [ARCHITECTURE.md](ARCHITECTURE.md) - Technical details for developers
