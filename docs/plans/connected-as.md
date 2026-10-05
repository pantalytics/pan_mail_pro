# Connected as

Status: **proposal, not built.** Written 2026-10-05 after a customer case:
Danielle signs in to Odoo as Anna, presses Connect Mailbox, and consents
with one of two Microsoft identities. Nothing on any screen says which.

## What is true today

- The address the provider reported at consent is already stored:
  `pan.mail.account.email` is what Graph `/me` answered, on the account row
  that hangs off the Odoo user. The backend knows. No screen shows it.
- My Preferences → Mail Pro says "connected" only by showing Disconnect.
  The user list has a boolean column. Settings counts "3 of 5 connected".
- On Microsoft a shared mailbox sends with the *author's* token
  (`_resolve_sending_user`): the draft is created in
  `/users/{shared}/messages` and sent from there. That needs two Exchange
  delegations on the shared address for the person whose token it is:
  **Full Access** (create the draft) and **Send As** (send it). Graph has no
  endpoint that lists Send As rights.
- The notification mailbox, and every personal mailbox, sends with its
  **owner's** token, whoever authored the mail. So the owner of
  notifications@ needs the same two delegations on that address, and if the
  owner's Odoo user is connected as somebody else, that somebody is who
  needs them.
- A denied send comes back as Graph's raw error text in `failure_reason`.

So the question "whose Microsoft identity is Anna's Odoo user connected
as" has an answer in the database and none on the screen, and "may that
identity send from sales@" has no answer anywhere until a send fails.

## The design

One sentence per screen, only where it changes what the reader does.

### 1. Show the address, not a boolean

`res.users.x_pan_mail_connected_as` (Char, stored compute from the
connected account's `email`, same depends as `x_pan_mail_connected`), and
`x_pan_mail_connected_elsewhere` (Boolean, not stored: the connected address
is not the user's own `email`/`login`). Both self-readable.

| Screen | Today | Proposed |
|---|---|---|
| My Preferences → Mail Pro | Disconnect button implies "connected" | `Connected as danielle@company.com`. Below it, muted, only when it differs: `Your Odoo user is anna@company.com.` |
| Settings → Users → form, Mail Pro tab | same as above | same as above |
| Settings → Users list | column "Mailbox Connected", a tick | column **Connected as**, the address. Empty cell = not connected. One column, says more |
| Settings → Mail Pro, Users block | `3 of 5 connected` | `3 of 5 connected`, plus one line only when it occurs: `1 connected as another address than their Odoo user` |

No warning, no dialog, no refusal. Connecting as another identity is
sometimes deliberate, and `_store_tokens` already refuses the one case
that breaks things (switching identity while the old one still works).

### 2. Which shared mailboxes this identity can use

A button, not a table. Under *Connected as*, a link **Check shared
mailboxes**. It asks the provider, with this account's token, for each
active shared mailbox on that provider plus the notification mailbox when
this user owns it, and answers in one notification:

```
You can send from sales@company.com, support@company.com.
No access to info@company.com. Ask an administrator for
Full Access and Send As on that address.
```

One contract method, `check_mailbox_access(account, mailbox) -> bool | None`.
Microsoft: `GET /users/{mailbox}/mailFolders/inbox?$select=id` with the
account's token. 200 is access, 403/404 is none. Gmail and IMAP return
`None` and the button is hidden: there a shared address is its own account
and the question does not arise (`supports_shared_mailbox`).

Honest about what it proves: the probe shows **Full Access**, which is the
delegation Exchange admins grant together with Send As and the one the
draft step needs first. A Send As missing on its own surfaces at the first
send, through item 3. Nothing is stored. A stored answer goes stale the
moment an admin changes a delegation in Exchange, and a stale "no" would
hide a mailbox from the composer that works.

The button sits on the user's own Mail Pro tab, so an administrator can
press it on Anna's user form too: it runs with the stored token, whoever
presses. That is how the admin checks Danielle's rights without Danielle.

Dropped: a "who can send from here" list on the mailbox form. The
question arrives from the person who cannot send, not from the mailbox.

### 3. A denied send says who and what

Graph answers `ErrorSendAsDenied` on the send and `ErrorAccessDenied` on
the draft. Map both, in the Microsoft client, to one readable
`failure_reason`:

```
danielle@company.com cannot send from sales@company.com.
An administrator grants Full Access and Send As on that address
in the Exchange admin center.
```

The identity named is the account's address, which is the one Exchange
knows. Today the reason reads as a Graph stack line and the admin goes
looking at Anna, who is not the person Exchange refused.

### 4. The mailbox says whose sign-in it sends with

One computed line on the mailbox form, `sends_with`, under the owner:

| Mailbox | Line |
|---|---|
| Personal, or the notification mailbox | `Sends with Anna's sign-in, danielle@company.com` |
| Shared, Microsoft | `Sends with each sender's own sign-in` |
| Shared, Gmail or IMAP | `Sends with its own account` |

Human language for `_resolve_sending_user`, which is the rule nobody can
see today. The notification mailbox is where this matters most: it is
"the owner's" in a way the shared ones are not, and the owner's connected
address is the one that needs the delegation.

## What this does not do

- No filtering of the composer's Send From dropdown on probe results.
  Prevention would be nicer than a failed send, but it needs a stored
  answer and a way to refresh it, and both are more machinery than the
  mistake deserves.
- No per-send record of (user, mailbox) outcomes. The error ledger
  already keeps `outgoing.send_failed` with the traceback; item 3 makes
  that row readable.
- No change to who may connect as whom.

## Changes

| Where | What |
|---|---|
| `models/res_users.py` | `x_pan_mail_connected_as`, `x_pan_mail_connected_elsewhere`, `action_check_mailbox_access()` |
| `models/mail_provider_client.py` | `check_mailbox_access(account, mailbox)`, default `None` |
| `models/providers/microsoft/graph_client.py` | the probe; `ErrorSendAsDenied` / `ErrorAccessDenied` → readable reason |
| `models/pan_mail_mailbox.py` | `sends_with` |
| `models/res_config_settings.py` | the "connected as another address" count |
| `views/res_users_views.xml` | the line, the muted line, the button, the list column |
| `views/pan_mail_mailbox_views.xml` | the `sends_with` line |
| `views/res_config_settings_views.xml` | the one extra line in the Users block |
| `tests/test_connect_banner.py` | connected-as and elsewhere, as the user and as an admin |
| `tests/test_provider_contract.py` | every client answers `check_mailbox_access` |
| `tests/test_microsoft_provider.py` | the probe on 200 / 403, the mapped failure reason |
| `docs/troubleshooting.md` | the readable reason, and the Check button as the first step |
| `ARCHITECTURE.md` §1 | the capability line |
| `__manifest__.py` | 19.0.24.0.0 |

Roughly 250 lines, no migration, no new model, no cron.
