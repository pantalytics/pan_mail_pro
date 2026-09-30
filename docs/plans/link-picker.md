# The link picker: unlink, change, create, and a crash

Status: **design, nothing built.** Four things the Change button on a linked
conversation gets wrong or does not do, in the order to build them. Each one
is a small pull request of its own; together they bump one minor version.

The picker today (`static/src/js/conversation_view/link_dialog.js`,
`pan.mail.conversation.link_targets` / `link_scope`,
`pan.mail.routing.log.link_to`): Change opens step one, a list of the kinds
of record; a kind picked opens Odoo's own `SelectCreateDialog` over that
model with the correspondent's records as a removable facet; the record picked
moves the whole conversation and repoints its thread link. Create is off by
choice, and there is no way to say "this belongs on no record".

## 4. The crash first: `Cannot read properties of null (reading 'selfFollower')`

**Cause.** The Followers button in the tab strip is the toggle slot of a
`Dropdown`, guarded by `t-if="recordThread"` on the `Dropdown` itself. The
guard runs when the Inbox renders; the slot runs when the `Dropdown` renders,
which is later, after its own `willUpdateProps`. `linkTo()` changes the
selection, refreshes, then re-selects, so between those two moments
`state.conversation.records` is empty and the getter answers `null`. The
slot then reads `.selfFollower` off it. Owl reports it as a lifecycle error
because the render that failed is the child's.

**Fix.** Evaluate the thread once per Inbox render and hand the slot the
value, not the getter:

```xml
<t t-set="thread" t-value="recordThread"/>
<Dropdown t-if="thread" ...>
    <button ... t-att-class="{ 'o_mailpro_followers_following': thread.selfFollower }">
```

A `t-set` variable is captured with the slot when the parent renders, so the
child cannot see a value the parent never rendered with. Keep the getter
null-safe as well (`recordThread?.selfFollower` nowhere else, the variable is
the rule). The same pattern is worth a grep: every getter read inside a slot
of a child component (`FollowerList`, the activity and file lists) has the
same window.

**Check.** `tools/ui_check.py` already links a conversation from the picker;
extend that step to link it *twice in a row* with the follower list open the
first time, and assert no error dialog. The page's `console` errors are
collected there already.

## 1. Unlink: "this belongs on no record"

**Recommendation.** Unlink is a row in step one, not a third button next to
Change. It reads **Only the contact** and names them: *Only Emovr B.V.*. It
skips step two and calls `link_to(messages, 'res.partner', partner_id)`. No
new backend, no new state.

Why the contact and not nothing: a `mail.message` with no model is readable
by its author and nobody else (CLAUDE.md, *Triage and correcting a match*),
so "unlinked" would make the conversation vanish for everyone but the person
who unlinked it. The contact is where the fetcher lands unmatched mail
(`outcome = 'fallback'`), so it is the state the rest of the module already
calls unlinked: the "On a contact only" folder, the link-coverage report, the
suggestion block. Unlink is a move to the fallback state, not a delete.

The thread link is repointed to the contact along with the messages, the
same as any other link. Deleting it instead would let the matcher guess
again on the next mail, which would land on the contact anyway through
rule 1, so repointing is the same outcome with one less code path.

Rules:

- The row shows only when the conversation is on something other than its
  contact. On a contact there is nothing to unlink, and Change is the only
  action.
- No contact (`partner_id` false, a conversation whose sender never became a
  partner): no row. The picker refuses nothing; the option is simply absent.
- One click, no confirmation. It is as reversible as any link: Change again.
- The confirmation toast reads *Linked to Emovr B.V. only. The next mail in
  this thread lands there too.*

Server side, nothing changes: `link_to` already accepts `res.partner` and
`_link_model` already admits it. `link_targets` gains an `unlink` entry at
the top of its answer when a `partner_id` is passed and the current record is
not that partner, so the client draws it from the same list and does not
decide on its own. One Python test: the row is there on a lead, absent on the
contact, absent without a partner.

## 2. Change: same kind of record, or another kind?

**Recommendation.** Do not answer this from taste. Keep the two steps, put
the conversation's current kind of record first in step one, and measure.

The two cases are real and both common. From the contact (the fallback state,
which is where most corrections start) the kind always changes: the mail
was on nobody's lead and belongs on one. From a real record the kind mostly
stays: two open quotes for one customer, the second opportunity of the same
company. Neither case is rare enough to hide behind an extra click, and
opening step two directly on the current model would make the first case two
screens instead of one.

What ships now:

- Step one lists the current kind of record first, labelled as such
  (*Lead, where it is now*), then the unlink row, then the rest as today.
  Same-kind corrections cost one click on the top row; a change of kind costs
  one click on any other row. No branching, no preference.
- `conversation_linked` gets one boolean, `same_model`. A boolean says
  nothing about the customer's Odoo, which is the rule the event's comment
  already states for the model name. After a month on real databases the
  number says whether step one should be skipped, and for whom.

The check: the current kind's row is first in `link_targets` when a
`current_model` is passed. One Python test.

## 3. Create from the picker

**Recommendation.** Turn Create on in step two, prefilled from the
correspondent. The comment in `link_dialog.js` that keeps it off ("a record
invented to hold it is a different decision") is right about linking and
wrong about triage: a mail from a new customer with no lead *is* the reason
to make the lead, and sending someone to the CRM app to create it and then
back here to link it is the round trip the Inbox exists to remove.

How:

- `noCreate: false` on the `SelectCreateDialog`. Odoo's own Create button
  opens the model's form in a dialog; on save the dialog hands the new id to
  `onSelected`, so `linkTo` runs unchanged. Verify that on the Odoo 19 build
  the module ships against (`select_create_dialog.js`, the `createEditRecord`
  path) before writing the browser step; if the build calls `onSelected`
  only on the select path, wrap the form dialog ourselves with
  `onRecordSaved`.
- `link_scope` returns `defaults` next to `domain`: `default_partner_id`
  for a model with a `partner_id`, `default_email_from` and
  `default_contact_name` for one with `email_from`, nothing cleverer, the
  same two relations the facet is built from. The client passes them as the
  dialog's `context`. A lead created from a mail opens with the sender
  filled in and the reader types a title.
- `link_scope` also returns `can_create` (`Model.has_access('create')`) and
  the client sets `noCreate` from it, so a reader who may link to leads but
  not create them sees no button that ends in an access error.
- Quick create by typing a name is left off. The record is a lead or a
  quote, not a tag; it wants a form.

Checks: one Python test on `defaults` per relation and on `can_create`; one
browser step in `tools/ui_check.py` that opens step two on a lead, clicks
Create, saves a lead with only a title, and asserts the conversation is now
linked to it and the lead carries the sender's email. That step is the one
that proves the round trip, and a Python test cannot see the dialog.

## Order and size

| Step | Files | Size |
|---|---|---|
| 4 | `conversation_view.xml`, `ui_check.py` | a morning |
| 1 | `link_targets`, `link_dialog.js` + xml, one test, one browser assertion | a day |
| 2 | `link_targets` (current first), `improve.js` event, one test | half a day |
| 3 | `link_scope`, `link_dialog.js`, one test, one browser step | a day |

Step 4 first because it is a crash on the path the other three extend. Steps
1 and 2 share the `link_targets` change and can ride one pull request. Step
3 is its own, because the browser step is the expensive part and the Odoo
dialog's create path has to be read first.

Not built, on purpose:

- Unlink to nothing (`model = False`). Invisible to everyone but the author.
- A "same model" shortcut that skips step one. Measured first, see 2.
- Creating a *contact* from the picker. The fetcher already made one for
  every sender; a second is a duplicate.
- Any AI rung under the picker. `docs/plans/mail-triage.md` says why, and
  nothing here changes it.
