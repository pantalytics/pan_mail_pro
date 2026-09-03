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

### The checklist is the status

A screen that reports how something is doing does not need a banner saying so.
A list of steps, each showing a check and its answer, says it in one look.

- **A banner repeats what the list already says.** Setup / Syncing was a
  headline above three lines that carried the same information.
- **A failure belongs on the line it happened to**, not in a summary at the
  top: a stopped mailbox is a red triangle on the mailboxes line, where the
  reader is already looking for it.
- **Two states per line, not four.** Answered, or open. Anything else is a
  sentence on the line.
- **Never a colour that means nothing.** Green for done, red for broken. There
  is no third.

### A finished step collapses into its answer

An answered setup step becomes one line: a check, its name, the answer itself,
and a pencil that reopens it.

```xml
<div class="o_mailpro_step" invisible="not x_setup_domains_done or x_edit_domains">
    <i class="fa fa-check-circle o_mailpro_step_check" title="Done"/>
    <span class="o_mailpro_step_name">4. Internal Domains</span>
    <span class="o_mailpro_step_value">
        <field name="x_internal_domain_ids" nolabel="1" readonly="1" widget="many2many_tags"/>
    </span>
    <field name="x_edit_domains" nolabel="1" widget="boolean_icon"
           options="{'icon': 'fa-pencil'}" class="o_mailpro_step_edit"/>
</div>
```

- **Show the answer, not just the heading.** Baymard tested this on accordion
  checkouts: a collapsed step that shows only its title makes people reopen it
  to see what they picked, which is worse than not collapsing at all.
- **`boolean_icon` is the pencil.** Odoo ships it; it binds a clickable icon to
  a boolean with no JavaScript of ours, and the click is a client-side
  re-render rather than a save.
- **Saving re-collapses everything**, because the transient record is rebuilt.
  That is the behaviour, not a bug: you edit one step, save, and the page is a
  list of answers again.

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
