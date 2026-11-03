# Design System

Design guidelines for Pantalytics Odoo modules. Combines Apple's user experience with Odoo's framework.

The brand guide at [brand.pantalytics.com/product-ui](https://brand.pantalytics.com/product-ui)
is the source: the five page types, the element rules, the writing standards.
This file is what applies inside Odoo, where a settings page is a `<group
string>` with rows rather than a card, and a danger zone is a red link with a
confirm. Capitalize only the first letter of a label, a button or a heading;
a button is verb plus object; no explanatory text under a title; stay silent
when it works.

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

### Icon buttons that move a boundary

One shape, one size, one place. Every control that folds a pane, opens a
drawer or steps between two panes is the same circle -- `$mailpro-control` in
`conversation_view.scss`, with the `mailpro-control` mixin -- whether it sits
in the top bar, in a pane header or on a divider. Two finishes and no third:
flat where it is in a bar or a header, raised where it floats over a pane.

It never sits on the line it moves. A circle centred on a divider is half over
each neighbour, and what is under those halves is a scrollbar on one side and
a header's first line on the other. It sits wholly inside one pane, and the
header it lands in keeps the room for it (`$mailpro-band-gutter`) whenever it
is there -- not only when a neighbour is folded, because a gutter that comes
and goes is a title that jumps.

Everything in a pane header sits on one band (`$mailpro-band`), so a folder
name, a subject and a fold button line up across the screen.

One chevron family. `fa-chevron-*` is unfold, fold and "move the boundary this
way", in the mailbox list, in the conversation list, on the dividers and on a
phone. A caret for the same gesture in one list and a chevron in the next is
the drift this rule exists to stop.

Motion is two lengths and no more: `$mailpro-fold` for a pane or a drawer
moving, `$mailpro-tap` for a pointer being answered. Both are off under
`prefers-reduced-motion`. The tap is the brand's 150ms ease; the fold is
longer on purpose, because it is the only motion that travels, and a pane
crossing 400px in 150ms reads as a jump.

Waiting is a skeleton, never a spinner: the shape of the answer, drawn after
`$mailpro-wait` (150ms) so a fast answer shows none, breathing so a slow one
reads as busy, and one sentence after `$mailpro-slow` (8s). A list being
replaced stays on screen dimmed rather than blanked. Every delay is in the
stylesheet, so there is no timer to clear when the answer lands.

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
  top: a stopped mailbox is a red dot on the mailboxes line, where the reader
  is already looking for it.
- **Three states per line, one shape.** Answered, broken, or open. Anything
  else is a sentence on the line.
- **Never a colour that means nothing.** Green for done, red for broken. There
  is no third.
- **Never a colour with one side.** Odoo compiles each stylesheet once per
  colour scheme. A colour is a `$mailpro-*` token that picks light or dark from
  `$o-webclient-color-scheme`, never a literal; `tests/test_dark_mode.py`
  fails on one, and `tools/ui_check.py` opens the Inbox in dark mode and
  fails on a light pane or a line of text below 3:1.

### The checklist is one column, and so is everything under it

Each step is one `o_mailpro_step` block inside a single `col-12 o_setting_box`
(`views/res_config_settings_views.xml`), not Odoo's two-column `<setting>`
grid. Deliberately: the checklist is one column of dots and names that the
eye scans top to bottom, and nothing on the page pairs with it. One column is
not full width, though: `o_mailpro_steps` is capped at 46rem
(`static/src/scss/setup_status.scss`), because a name on the left and an
arrow on the far right of a 2000px window is two things, not one line. That
was a shipped bug, and `tools/ui_check.py` asserts the cap at 2000px. The
About and Help improve Mail Pro lines below it are `col-12` for the same
reason: one line each, nothing to pair them with.

The two-column pattern in CLAUDE.md (*Odoo Settings Page Layout*) is for
settings that are a pair of equal forms side by side. Nothing on this page is
that today; reach for it only when two blocks of fields are genuinely
alternatives to each other, and never for a step of the checklist.

### Every step is the same line

A setup step is one line, and all of them are the same line: a coloured dot, its
name, the answer itself at the end of the line, and a chevron that is the way
to the place it is changed. The rows sit in one rounded group with a 1px
border, the way a phone's Settings app lists them.

```xml
<div class="o_mailpro_step">
    <span class="o_mailpro_dot o_mailpro_dot_ok" role="img" aria-label="Done" title="Done"
          invisible="not x_setup_domains_done"/>
    <span class="o_mailpro_dot o_mailpro_dot_todo" role="img" aria-label="To do" title="To do"
          invisible="x_setup_domains_done"/>
    <div class="o_mailpro_step_body">
        <div class="o_mailpro_step_head">
            <span class="o_mailpro_step_name">2. Internal Domains</span>
            <span class="o_mailpro_step_value" invisible="not x_setup_domains_done">
                <field name="x_internal_domains_summary" nolabel="1" readonly="1"/>
            </span>
            <span class="o_mailpro_step_value" invisible="x_setup_domains_done">Not set yet</span>
        </div>
        <div class="o_mailpro_step_msg" invisible="x_setup_domains_done">
            The domains your company owns. Email between them is never copied into Odoo.
        </div>
    </div>
    <button type="action" name="%(action_pan_mail_domain)d"
            icon="oi-chevron-right" title="Internal Domains"
            class="oe_link o_mailpro_step_edit"/>
</div>
```

- **Show the answer, not just the heading.** Baymard tested this on accordion
  checkouts: a collapsed step that shows only its title makes people reopen it
  to see what they picked, which is worse than not collapsing at all.
- **One shape in four colours, always in the same place.** Green for answered,
  red for answered-but-broken, an outlined circle for not yet, and amber
  (`o_mailpro_dot_attention`, the brand gold `#e8a317`) for a step that works
  and has something to do: a provider nobody has signed in on yet (the
  same status the provider form's amber badge shows, never a second
  opinion of it), a personal mailbox carrying the system email (read off
  the notification mailbox's own type; a shared one carrying the flag is
  green), or a provider row still on its own Azure app. Its
  one line under the name ("Sign in again to finish the move") is
  `o_mailpro_msg_attention`, a darker amber on a light page so the text keeps
  its contrast. Amber never stands in for red: if
  something has stopped, the dot is `o_mailpro_dot_warn`. A column of
  different glyphs reads as a column of different kinds of thing; a column of
  dots reads as one status you can scan in a second. Not-done is an outline
  rather than a fill, because a setup page that opens in red reads as a product
  that is already failing.
- **The answer is one word or one number, never a list.** The account line
  reads `Connected`, the domains line `3 domains`, the mailboxes line
  `4 mailboxes` -- not the plan and its dates, not `company.com, company.nl`,
  not a row of tag chips. A list does not fit on a line and a count always
  does; the list is one chevron away. Chips beside a bold heading look like
  part of it, and the four lines stop matching.
- **The answer sits at the end of the line, in grey.** Four names of
  different lengths put four answers on one right edge, so the column of
  names stays a column without a fixed width. An answer too long for the
  line is cut with an ellipsis, never wrapped: the page behind the chevron
  holds the whole of it. In the mailbox's own setup flow, where the answer
  is a field you type in, it stays beside the name instead.
- **Red is for broken, never for a choice.** A mailbox on a domain the admin
  left off the internal list is treated as external, which is the setting
  working. It got a red warning line for one commit, and a warning about
  correct behaviour is worse than no warning at all.
- **A sentence goes under the name, never beside it.** The answer is short
  enough to sit on the first line; a fix or an alert is not. Beside the name a
  long one pushes the arrow off the line and turns the column of names ragged.
- **The arrow goes to the data.** Steps 2 to 4 are tables — providers,
  domains, mailboxes — so every line links to the table rather than growing an
  editor of its own on this page. The settings page shows the answer; the
  table owns it. The provider used to be the one exception, opening its
  credentials in place; once the credentials became a `pan.mail.provider`
  row like the other two answers, the exception had no reason left to exist.
  The account was the next one: a connected step 1 carried who connected it,
  the valid-until date, Usage and billing and Disconnect on the line, four
  rows tall beside three lines of one. Those went to the account page
  (`pan.mail.license`'s form) behind an arrow of its own. Only an open step
  1 keeps its button on the line, because a pairing in progress has no row
  worth opening yet.
- **The overview reports, the page behind the arrow acts.** A step shows a
  dot, its answer and at most one short line; the button that fixes it is
  one level deeper, the way a phone's Settings app puts a badge on the row
  and the action on the page it opens. Step 2 on an own Azure app was a
  deprecation, a Sign in with Microsoft button, a count and a cleanup link
  on the overview for one release (#369). The line and the button are on
  the provider form now; the count went, because who still has to sign in
  is each mailbox's status, and the cleanup happens by itself once nobody
  needs the old app. No confirm on a button that only sends somebody to
  sign in. The exceptions are steps with no page to open: an open step 1, and a
  broken one, whose Connect again stays on the line. An arrow opens the row
  itself when there is one row, never a list that can land on a new form.
- **One route: a row with an arrow holds nothing else, and the whole row is
  the way in** (#382). The overview still carried shortcuts after #369:
  "Use Microsoft 365" under step 2, "Add <domain>" under step 3, Choose a
  plan, Add a card and Check again under step 1. Two routes to one step, and
  the shortcut was the worse one: "Use Microsoft 365" ended on a form with
  nothing to press, and "Add" offered the Odoo.sh host as the company's
  domain. The chevron's hit area covers the row (`::after`), so the dot, the
  name and the answer all open the same page, and that page has the one
  primary button: Sign in with Microsoft on the provider form (the row is
  made and saved first when there is none), Add <domains> beside New on an
  empty Internal domains list, Choose a plan, Add a card or Check again on
  the account page. `tools/ui_check.py` (`rows_only_report`) fails on a
  button or link beside an arrow and on a row whose name does not land on
  it.
- **No choice is shown that a normal customer should not make; a legacy
  state shows as deprecated with one action.** The provider form had a
  "Sign in through" radio between Mail Pro's Microsoft app and an own Azure
  app. Nobody new should pick the second, so the radio is gone; a database
  still on it reads "deprecated", sees its old fields read-only, and gets
  Sign in with Microsoft.
- **A fix lives only while it is a fix.** The Internal domains list offers
  the domains it can read off the database, and only while it is empty. A
  list with something in it is the admin's, and offering additions to it
  forever would read as a complaint about a deliberate choice.

### Explanation is one muted line, never a tooltip

A `title` attribute never shows on touch and is unreliable for keyboard and
screen-reader users, so a setting explained only there is explained to nobody
(`tools/ci_lint.sh` refuses the pairing). One short sentence under the control,
in `text-muted`, while the step is open; once it is answered the answer beside
the name explains it better than any sentence, and the line goes away.

- **On screen**: the label, the field, the button, and that one line.
- **The one exception is a switch whose label already says what it does.**
  Help improve Mail Pro is a toggle at the bottom of the page: the label is
  the explanation, and what it does *not* collect sits behind an info icon
  with a `title`, the way Odoo's own settings mark a company-specific field
  (Odoo forbids its `data-tooltip` attributes in an arch, so `title` is the
  only tooltip a view can carry). It does not show on touch, and that is
  acceptable only because the switch is answerable from its label alone.
  Never for a control the reader has to understand before answering; the
  lint exempts this one switch by its label.
- **Still on screen, in `text-warning`**: a consequence somebody is about to
  walk into — a confidential-mail warning, a step that is blocking. One short
  line, not an `alert` box.

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
