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

### Every step is the same line

A setup step is one line, and all of them are the same line: a coloured dot, its
name, the answer itself, and the way to the place it is changed.

```xml
<div class="o_mailpro_step">
    <span class="o_mailpro_dot o_mailpro_dot_ok" title="Done"
          invisible="not x_setup_domains_done"/>
    <span class="o_mailpro_dot o_mailpro_dot_todo" title="To do"
          invisible="x_setup_domains_done"/>
    <div class="o_mailpro_step_body">
        <div class="o_mailpro_step_head">
            <span class="o_mailpro_step_name">2. Internal Domains</span>
            <i class="fa fa-info-circle text-muted" title="One row per domain your company owns..."/>
            <span class="o_mailpro_step_value" invisible="not x_setup_domains_done">
                <field name="x_internal_domains_summary" nolabel="1" readonly="1"/>
            </span>
            <span class="o_mailpro_step_value" invisible="x_setup_domains_done">Not set yet</span>
        </div>
        <div class="o_mailpro_step_msg" invisible="x_setup_domains_done or not x_internal_domains_suggested">
            <button name="action_apply_suggested_internal_domains" type="object"
                    class="btn-link btn-sm p-0" string="Add"/>
            <field name="x_internal_domains_suggested" class="d-inline" readonly="1" nolabel="1"/>
        </div>
    </div>
    <button type="action" name="%(action_pan_mail_domain)d"
            icon="oi-arrow-right" title="Internal Domains"
            class="oe_link o_mailpro_step_edit"/>
</div>
```

- **Show the answer, not just the heading.** Baymard tested this on accordion
  checkouts: a collapsed step that shows only its title makes people reopen it
  to see what they picked, which is worse than not collapsing at all.
- **One shape in three colours, always in the same place.** Green for answered,
  red for answered-but-broken, an outlined circle for not yet. A column of
  different glyphs reads as a column of different kinds of thing; a column of
  dots reads as one status you can scan in a second. Not-done is an outline
  rather than a fill, because a setup page that opens in red reads as a product
  that is already failing.
- **The answer is text, not a widget.** The domains line reads
  `company.com, company.nl`, not a row of tag chips: chips beside a bold
  heading look like part of it, and the three lines stop matching.
- **Red is for broken, never for a choice.** A mailbox on a domain the admin
  left off the internal list is treated as external, which is the setting
  working. It got a red warning line for one commit, and a warning about
  correct behaviour is worse than no warning at all.
- **A sentence goes under the name, never beside it.** The answer is short
  enough to sit on the first line; a fix or an alert is not. Beside the name a
  long one pushes the arrow off the line and turns the column of names ragged.
- **The arrow goes to the data.** All three steps are tables — providers,
  domains, mailboxes — so every line links to the table rather than growing an
  editor of its own on this page. The settings page shows the answer; the
  table owns it. The provider used to be the one exception, opening its
  credentials in place; once the credentials became a `pan.mail.provider`
  row like the other two answers, the exception had no reason left to exist.
- **A fix line lives only while it is a fix.** The domains line offers the
  domains it can read off the database, and only while the list is empty. A
  list with something in it is the admin's, and offering additions to it
  forever would read as a complaint about a deliberate choice.

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
