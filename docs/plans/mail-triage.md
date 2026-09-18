# Triage: where mail goes when the headers do not say

Status: **design only.** Nothing here is built. The ladder's first three rungs
already ship (ARCHITECTURE.md §4); this document is about the residue underneath
them, the screen that shows and corrects a match, and whether a language model
belongs in it.

Gated on a number we have not read yet. `pan.mail.coverage` exists to say what
share of mail files itself correctly today. If that number is 90%, everything
below is a 10% problem and the effort belongs elsewhere. **Read it on a real
customer database before building any of this.**

## 1. The ladder, checked

The rules as stated: a reply goes on the record it answers; a mailbox bound to
an app creates the record that app makes; everything else is triage. That is the
right shape and it is roughly what the code does, with the second and third
steps thinner than they read.

What holds:

- **Deterministic first, always.** A `References` chain is exact, free and
  reproducible. Nothing probabilistic may sit above it.
- **The alias rung is a mailbox property, not a guess.** `support@` becoming a
  ticket is configuration, so it costs nothing and never needs review.
- **The residue is small and expensive.** Few mails, but each one is a customer
  waiting on a record nobody opened.

What is missing, in the order it is worth adding:

**A rung between 2 and 3, deterministic and unbuilt: a record reference in the
subject.** `Re: SO1234`, `Ticket #445`, a quote number pasted into a fresh mail.
Exact, cheap, a lookup rather than a guess, and today it falls all the way
through to contact chatter. This is the cheapest matching we do not have.

**A second one: the only open record.** Sender is a known contact, the mailbox
routes to `helpdesk.team`, and that contact has exactly one open ticket. That is
not an inference, it is arithmetic. A model should only ever be asked about the
zero-or-many case.

**The answer space is three-way, and the ladder only has two.** A triage tier
can say *attach to X*, *create a new T*, or *this belongs nowhere*. The third is
the one that saves the most time, because a newsletter, a portal notification
and an invoice from a supplier all currently land on somebody's contact chatter
and each one costs a person a look. Nobody builds it and it is the cheapest to
be right about.

**Nothing learns.** A person who corrects a match today changes one message. The
correction should write a `pan.mail.thread.link` row, so the next mail in that
conversation matches at rule 3 forever, deterministically, for free. One click
buying permanent correctness for a thread is the whole self-configuring bet in
miniature, and the table it needs already exists.

**The ladder is incoming only.** A sent item that starts a new conversation has
the same question and no answer ([#106](https://github.com/pantalytics/pan_mail_pro/issues/106)).
One ladder should serve both directions rather than two.

**Delivery must not wait on triage.** The mail already lands somewhere: contact
chatter, findable, attached to the right person. That floor is safe and stays.
Triage proposes a better home afterwards; it never becomes the thing that
decides whether a mail arrives.

## 2. The screen

**One surface: the Inbox, not the routing log.** `pan.mail.routing.log` is an
audit table under Settings → Technical and nobody works a queue there. Twice now
(19.0.7.0.0) this module has learned that. The inbox already has the two folders
this lives in, `On a contact only` and `Linked to nothing`, and
`pan.mail.conversation._rejected_for()` already reads what the matcher turned
down.

**The linked-to chip.** Every conversation row and the thread header carry one
chip: the record the thread is linked to, and why, in the words
`ROUTING_RULES` already holds ("The reply headers of the thread"). The chip is
the affordance. Clicking it opens a popover with the candidates the matcher
rejected, a `Link it here` on each, and a record search for the case where none
of them is right.

**Three states, not a percentage.**

| State | Reads as | Action |
|---|---|---|
| Linked | the record name, plain | open it |
| Suggested | dotted chip, "Looks like <record>" | Accept, or Choose another |
| Unlinked | grey, "Not linked" | Choose a record |

A number on screen is a number people argue with. The confidence stays in the
popover and in the routing log, one level deeper, for the person debugging.

**Correcting says what it bought.** The confirmation is one line:
"Linked to X. The next mail in this thread lands here too." That is the visible
payoff for the click, and it is true because the correction wrote a thread link.

**Bulk is multi-select in the unlinked folder, and that is all.** Pick several,
link them to one record.

**Dropped on purpose: a per-mailbox rules builder.** Conditions and actions in
the UI is a support surface a two-person team cannot carry, and every customer
builds a different broken version of the ladder that is already in the code.
What stays configurable is the mailbox's alias target and the block list.

## 3. Does the backend fit

Mostly yes. Three gaps, one of them real.

Fits:

- `pan.mail.matcher._match_rules()` is a named, ordered list and the AI rung's
  shape is already written into its docstring.
- `pan.mail.routing.log` already stores rule, confidence, every rejected
  candidate and the reason. The audit surface a probabilistic tier needs is
  there before the tier is.
- `pan.mail.thread.link` is the learning store. A correction has somewhere to go.
- `pan.mail.coverage` is the gate on whether to start.

Gaps:

1. **There is no write path.** Nothing can move a message to another record
   after the fact. Relinking means writing `model` / `res_id` on the
   `mail.message`, moving the thread link, fixing followers, and marking the log
   reviewed, under the access rights of a person who may not be able to read the
   target. `pan.mail.conversation`'s own rule is that reads happen there and
   writes do not, so this is a new method on the routing log, calling Odoo's own
   methods rather than reimplementing them. This is the piece that must exist
   whether or not a model is ever involved, because a human correcting a match
   is the feature. **Build this first, alone. It is useful without AI.**
2. **There is no second pass.** The matcher runs inside the fetcher's
   one-minute cron inside a savepoint, where a slow call stalls a mailbox and a
   failure rolls the message back. ARCHITECTURE.md §8 already forbids AI there
   and it is right. Triage is a separate cron over rows the first pass left
   unfiled, and it writes a suggestion, never a delivery.
3. **The key plumbing went with the seam in 19.0.7.0.0.** It comes back smaller:
   one parameter, one call site, no registry, no abstract contract until there
   is a second implementation.

## 4. The model, and what confidence means

**Do not use Odoo's AI app.** It is Enterprise only
([Odoo 19 docs](https://www.odoo.com/documentation/19.0/applications/productivity/ai/agents.html),
[forum](https://www.odoo.com/forum/help-1/community-edition-v19-ai-module-missing-292901)).
This module ships on Community and Enterprise, CI runs the community image, so
depending on it makes the feature absent for half the customers and untestable
in the build. It is also a chat and agent surface rather than a classifier with
structured output and a latency budget, and it bills through Odoo's account
while Mail Pro is sold direct and already has an entitlement channel of its own.
One function, one provider, bring-your-own-key.

Four properties, each of which is where this normally goes wrong.

**The model picks from a shortlist, it never names a record.** The candidates
come from the deterministic rules that already ran: open records for this
contact, anything referenced in the subject, recent conversations in this
mailbox. It returns one of them or `none`. An id outside the list is discarded.
Confidence over a closed set means something; over the whole database it is
noise.

**The model's own confidence is not the routing number.** It is one input.
The score that decides is combined with what we already know: is the sender a
known contact, is the record open, how old is it, was there exactly one
candidate. Those are the signals that calibrate; the model's self-report is the
one that does not.

**Cap the first release below the auto-route bar.** `AUTO_ROUTE_CONFIDENCE` is
0.8; the AI rung returns at most 0.79 and therefore only ever proposes. You get
the accept-rate data before you get the incident. Raise the bar per class later,
once the routing log says which classes earned it, and never globally.

**Measure two numbers, neither of them accuracy.** The acceptance rate of
suggestions, which says whether the tier is worth its key. And the silent
duplicate rate, `created` with candidates, which the log already flags as
`needs_review` and which is the expensive error: a second ticket opened on a
conversation that was already running.

### The privacy question this forces

ARCHITECTURE.md §8 says envelope only, never a body. Triage on content cannot
honour that. A subject line alone does not separate "reply to the quote" from
"new problem with the same machine", which is the case the tier exists for.

So it is a decision, not a PR. Either the tier stays envelope-only and is
noticeably weaker, or a body goes out and the module says so. The
recommendation is the second, with three limits: opt-in per database and off by
default, the first 2000 characters after the quoted history is stripped (the
`QUOTE_START` regex already does this for the preview), and never an
attachment. Then the manifest's data-disclosure paragraph says exactly that,
in the same words as the heartbeat's.

## Order of work

1. Read `pan.mail.coverage` on a real customer database. If the residue is
   small, stop here.
2. The record reference rule and the only-open-record rule. Deterministic,
   testable without a provider, no key.
3. The linked-to chip and relinking, writing a thread link. Useful alone.
4. The second-pass cron, suggesting from a shortlist, capped below auto-route.
5. Only then: raising the bar for the classes that earned it.
