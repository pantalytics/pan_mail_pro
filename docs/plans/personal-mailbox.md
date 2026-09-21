# Your own mailbox, in the Inbox

Status: **ships in 19.0.16.0.0**, read side and screen. What is deliberately
not in it is under "What this is not"; nothing else of this file is
outstanding.

## The problem

The Inbox shows the mail Odoo imported, and Odoo imports a deliberate subset:
replies to what it already holds, plus whatever rung of `sync_level` the
mailbox was given. Internal mail never enters, and neither does mail you
started yourself that is not a reply (issue #106). So the list is always a
part of your mailbox, and a part of a mailbox is not something anybody can
work in: you keep the real client open beside it, which is the thing this
module exists to stop.

The obvious fix -- import everything -- is the wrong one. It copies internal
mail, HR mail and private mail into a database where the fallback home for an
unfiled message is the sender's own contact chatter, which every internal user
can read. The gate that refuses internal mail is not an obstacle to this
feature; it is the reason the feature cannot be built that way.

## The decision

**Your own mailbox is read live from the provider and never stored.**

The client contract already implements the whole mail-client surface on all
three providers: `list_folders`, `search_messages`, `fetch_messages`,
`get_message`, `set_seen`, `set_flagged`, `move_messages`, `delete_messages`.
Nothing new has to be written at the provider seam. The Inbox asks the
provider for a page of your mailbox and draws it.

That settles three questions at once.

- **Privacy.** Nothing enters the database, so the heartbeat model, the
  internal-domain gate and the retention story are all unchanged. The mail is
  in your mailbox, where it already was.
- **Access.** The rule is one line: the mailbox must be `personal` and its
  owner must be the calling user. A shared mailbox is refused outright, and no
  other Odoo user can call the method for your mailbox because ownership is
  the check, not a group. This is what the group on the menu never was.
- **The filter.** Each row is looked up in the Message-ID ref index, so it
  carries `linked` -- the record it is filed on, or `False`. "In Odoo" and
  "Not in Odoo" are a filter over a flag the row already has.

**Reading is private; filing is public.** A live row that is not in Odoo can
be imported with one action, which runs the existing fetcher with
`pan_mail_force_import`. From that moment it is ordinary Odoo data under
ordinary Odoo rules -- a mail from a colleague filed this way lands on that
colleague's contact and is readable by whoever may read contacts. The screen
says so at the moment of the click, because that is the only moment it
matters. The block list is still not overridable: `force_import` lifts the
filters, never the objection.

## What this is not

- **Not a second inbox to keep correct.** There is no mirror table, no flag of
  ours on a provider message, no cursor. A read is a read.
- **Not a reply path for mail Odoo does not have.** Replying to a live,
  unlinked message means importing it first (one click) and replying to the
  conversation. A send with no record behind it is the "linked to nothing"
  state the module refuses to create, and it would need a `mail.mail` with no
  `res_id` to carry it. Dropped on purpose.
- **Not paged past the first screen.** `search_messages` takes a `limit` and
  no `offset`, and giving the contract an offset means three provider
  implementations and their tests. The live list is the newest 50, with
  search. Deeper history is a search term, which is how people find old mail
  anyway.
- **Not shared mailboxes.** Somebody has to own a mailbox for "your own mail"
  to mean anything. A shared mailbox is read the way it is read today: through
  what the sync imported.

## The screen

One folder and one filter menu, and that is the whole of it.

- Under your own personal mailbox, below Inbox and Sent: **All email**. Only
  there -- a shared mailbox does not offer it, which is the access rule
  visible on screen. It carries no count: counting it means asking the
  provider how much mail you have every time a mailbox is unfolded.
- The rows are the rows the list already draws. What a live row adds is a
  quiet **Not in Odoo** chip; what it lacks is a message id, which is how the
  screen tells the two apart.
- The filter menu over that folder asks the one question the folder exists
  for: **Not in Odoo** / **In Odoo**. Crossing between an imported folder and
  this one clears the filter rather than carrying a question the other menu
  cannot show.
- A row that is in Odoo opens the conversation that exists, with the record
  pane beside it: unchanged behaviour, reached from a longer list.
- A row that is not opens the message read-only, with **Add to Odoo** under
  it and the line that makes filing a decision: *Not in Odoo. Only you can
  see this email.*

Search reaches the provider instead of `mail.message` for that folder, which
is the one place a live folder is better than the imported list: it searches
the whole mailbox rather than the part that was imported.

A folder that waits on a provider can fail in ways a folder that waits on
Postgres cannot -- an expired grant, a network that is down. That is this
folder being unavailable, not the screen breaking: it says the mailbox is not
connected and the imported folders beside it still read.
