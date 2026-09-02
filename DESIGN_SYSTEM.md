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
