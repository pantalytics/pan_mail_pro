# Design System

Design guidelines for Pantalytics Odoo modules. Combines Apple's user experience with Odoo's framework.

## Philosophy

### From Apple we learn:

1. **Clarity** - Every screen has one clear purpose
2. **Deference** - Content is the star, not the interface
3. **Depth** - Hierarchy through whitespace and typography, not color

### From Odoo we keep:

1. Standard components and colors
2. Existing navigation patterns
3. Technical conventions (field names, XML structure)

---

## Principles

### 1. One primary action per screen

```xml
<!-- Good: One clear main action -->
<header>
    <button name="action_connect" type="object" class="btn-primary">
        Connect
    </button>
</header>

<!-- Avoid: Multiple primary buttons -->
<header>
    <button class="btn-primary">Save</button>
    <button class="btn-primary">Connect</button>
    <button class="btn-primary">Sync</button>
</header>
```

### 2. Progressive disclosure

Show only what's relevant now. Hide complexity until the user asks for it.

```xml
<!-- Basic fields always visible -->
<field name="name"/>
<field name="email"/>

<!-- Advanced only when needed -->
<group string="Advanced" invisible="not show_advanced">
    <field name="technical_setting"/>
</group>
```

### 3. Human language

| Avoid | Use |
|-------|-----|
| `x_pan_mail_connected` | "Mailbox connected" |
| `sync_interval_minutes` | "Sync every..." |
| `is_active` | "Active" |
| `Error: Invalid token` | "Session expired. Please sign in again." |

### 4. Smart defaults

Choose the most common option as default. Users only need to act on exceptions.

```python
# Good: Sensible defaults
sync_enabled = fields.Boolean(default=True)
sync_interval = fields.Selection(default='15')  # Most chosen option

# Avoid: User must always choose
sync_interval = fields.Selection(required=True)  # No default
```

### 5. Prevention over correction

Prevent errors instead of reporting them afterwards.

```xml
<!-- Good: Button disabled when action not possible -->
<button name="action_sync"
        invisible="state != 'active'"
        string="Sync"/>

<!-- Avoid: Button always visible, error message on click -->
```

---

## Components

### Forms

**Structure:**
```xml
<form>
    <!-- Status indicator at top -->
    <field name="state" widget="statusbar"/>

    <sheet>
        <!-- Main info: no group label needed -->
        <group>
            <field name="name"/>
            <field name="email"/>
        </group>

        <!-- Secondary info: with label -->
        <group string="Settings">
            <field name="setting_1"/>
            <field name="setting_2"/>
        </group>

        <!-- Tabs for related data -->
        <notebook>
            <page string="History">
                <field name="log_ids"/>
            </page>
        </notebook>
    </sheet>
</form>
```

**Guidelines:**
- Max 6-8 visible fields without scrolling
- Group logically related fields
- Use `placeholder` for examples, not instructions

### Lists

```xml
<list>
    <!-- Primary identifier always first -->
    <field name="name"/>

    <!-- Max 5-6 columns -->
    <field name="email"/>
    <field name="state" widget="badge"/>

    <!-- Date on the right -->
    <field name="last_sync"/>
</list>
```

**Guidelines:**
- Fewer columns = easier to scan
- Use badges for status
- Avoid technical IDs in lists

### Buttons

| Type | Use | Class |
|------|-----|-------|
| Primary | Main action of the screen | `btn-primary` |
| Secondary | Alternative actions | `btn-secondary` |
| Link | Navigation, cancel | `btn-link` |
| Danger | Destructive actions | `btn-danger` (sparingly) |

### Messages

```python
# Success: short and positive
self.env.user.notify_success("Connected")

# Warning: explain what to do
self.env.user.notify_warning("No mailbox selected. Choose a mailbox to sync.")

# Error: be specific, offer solution
raise UserError("Cannot connect to Microsoft. Check your internet connection and try again.")
```

---

## Patterns

### Wizard flow for complex tasks

Use a wizard when:
- More than 3 steps are needed
- Steps depend on each other
- User needs guidance

```
Step 1: Choose account    →    Step 2: Configure    →    Step 3: Confirm
     [Next]                        [Back] [Next]           [Back] [Finish]
```

### Confirmation for destructive actions

```python
# Always ask confirmation for:
# - Deleting data
# - Undoing configuration
# - Bulk operations

def action_disconnect(self):
    return {
        'type': 'ir.actions.act_window',
        'name': 'Disconnect?',
        'res_model': 'pan.mail.disconnect.wizard',
        'view_mode': 'form',
        'target': 'new',
    }
```

### Empty states

When a list is empty, show a helpful message:

```xml
<list>
    <field name="name"/>
    <!-- Odoo shows "No records found" automatically -->
    <!-- Consider adding a help action -->
</list>
```

### Status, not banners

A screen that reports how the module is doing says it once, at the top, in one
line. Three states and no fourth: **Setup**, **Syncing**, **Attention needed**.

```xml
<div class="o_mailpro_status">
    <div class="o_mailpro_status_head">
        <span class="o_mailpro_dot o_mailpro_dot--syncing"
              invisible="x_setup_status != 'syncing'"/>
        <field name="x_setup_status" nolabel="1" readonly="1"
               class="o_mailpro_status_label"/>
    </div>
    <div class="o_mailpro_status_detail">
        <field name="x_setup_status_detail" nolabel="1" readonly="1"/>
    </div>
</div>
```

Rules that make it read as one thing rather than three:

- **A headline and one sentence.** The headline is the state; the sentence says
  what to do about it. Nothing else goes on the line.
- **The dot is the only colour.** No coloured background behind text, no icon
  set, no border. Accent purple `#5b58d8` for setup, green `#1f9d63` for
  syncing, red `#c0392b` for attention.
- **Stacked alerts are the failure mode this replaces.** Three `alert-warning`
  boxes at the top of a page are three things shouting; one status line with a
  changing sentence is one thing speaking.
- **Never invent a fourth state.** If something new can go wrong, it is a
  sentence under "Attention needed", not a new colour.
- **A finished checklist folds away.** Once every step is answered the steps
  collapse and the status line is the page; a toggle brings them back. Use a
  plain `fields.Boolean` on the transient record and one `invisible=` on a
  wrapper div — that is a client-side re-render, where a button would save the
  form on its way to the server.

### Explanation belongs behind an info icon

A paragraph of help text under every field turns a settings page into a manual.
Put the label on the screen and the reason one hover away:

```xml
<span class="o_form_label">Your domains</span>
<i class="fa fa-info-circle text-muted ms-1"
   title="Comma separated. Email between these domains is not synced into Odoo."/>
```

- **On screen**: the label, the field, the button. Nothing else.
- **Behind the icon**: why it exists, what the wrong answer costs, what format to
  type.
- **Still on screen**: a consequence somebody is about to walk into — a
  confidential-mail warning, a step that is blocking. Those are one short line
  in `text-warning`, not an `alert` box.

---

## Checklist

Use for every module review:

- [ ] Does each screen have one clear primary action?
- [ ] Are labels human-readable (no technical field names)?
- [ ] Are smart defaults configured?
- [ ] Is complexity hidden until needed (progressive disclosure)?
- [ ] Are error messages helpful and actionable?
- [ ] Does the flow work logically without reading documentation?
- [ ] Are destructive actions protected with confirmation?

---

## References

- [Apple Human Interface Guidelines](https://developer.apple.com/design/human-interface-guidelines)
- [Odoo UI Guidelines](https://www.odoo.com/documentation/17.0/developer/reference/user_interface.html)
