# MVP: where both repos stand, and what is left

Status: **review of 2026-09-18**, after the September PR run (50 merges on
pan_mail_pro, 13 on mail-pro-admin). Written to be read cold. Facts were
checked against the code, the live instances and the admin service, not
against the plan documents, several of which had drifted (fixed in the same
change).

## The MVP, in one sentence

A customer runs Mail Pro on an Odoo instance connected to a Pantalytics
account, on a plan we set by hand, and we can see what they send. Nothing
self-serve, no Stripe, no download page. Selling by hand first is what
[mail-pro-paid.md](https://github.com/pantalytics/mail-pro-admin/blob/main/docs/plans/mail-pro-paid.md)
already decided; this file only says how far that is from here.

## Health of the repos

| | pan_mail_pro | mail-pro-admin |
|---|---|---|
| Mainline | `19.0` at 19.0.13.1.0, CI green, release tagged | `main`, CI green, deployed to app.mailpro.pantalytics.com |
| Open PRs | none | none |
| Open issues | 14 (4 stale ones closed in this review) | 3 |
| Tests | 732 in 48 files, plus the browser check in CI | 104 (20 integration), plus the browser check in CI |
| TODO/FIXME in code | none | none |
| Untested by construction | the OWL Inbox has no JS tests; only the Playwright walk and the Python read API | billing, download, revoke, multi-workspace: none exist |

Both repos are in good shape as codebases. The gap is not quality, it is
that the two halves of the licence contract were built against different
versions of the plan.

## What is built, against the product brief

The brief sells three things. Two ship, the third ships as a screen.

**1. Setup that finishes.** Built. Three providers behind one contract, a
setup checklist with three steps, the fail-closed domain list, the SMTP
takeover at first mailbox. Docs per provider under `docs/getting-started/`.

**2. Mail from a person, landing on a record.** Built. Outgoing routing with
one reason per failure, the incoming gate ladder, the matcher with the
References rungs plus the two deterministic triage rungs from #144, the
routing log, and the boundary that an imported message notifies nobody
(the Juffermans incident, closed in #37).

**3. The conversation with the record beside it.** Built as of 19.0.10 and
polished through 19.0.13: four panes, folder rail, filters, search, New
Email, reply in the pane, the four tabs, linking in two steps, the
suggestion chips. What the plans still call "on paper" and is actually
built: New mail, search, filter, linking, the two triage rungs and the
correction. What is genuinely not built: the chatter's door into the Inbox
(`record_conversations` has no caller), the customer timeline screen
(`customer_timeline` has no caller), Cc in the composer, and any AI rung.

**Connect and entitlement.** Built end to end and live: the Connect button,
the pairing flow, the Ed25519-signed answer, the daily heartbeat, the gate
(no connection means no incoming sync and no new mailbox; sending keeps
working). Pantalytics is connected: status `active`, plan `free`, five
healthy mailboxes, heartbeat today.

## What is not built, and whether the MVP needs it

| Item | Where | MVP? |
|---|---|---|
| Module reads `seats_allowed`; server signs `daily_send_limit` | pan_mail_pro #169 | **Yes.** The limit never reaches the module |
| Heartbeat carries no send/receive counts or error codes | pan_mail_pro #169 | **Yes.** The dashboard's "sent today" column and `mailpro_usage_daily` are fed zeros |
| Send throttle (defer over the limit) and the one-month trial | pan_mail_pro, phase 2 | **Yes**, after the two above. Free has to bite or nobody pays |
| A way to put an installation on Pro or Max | mail-pro-admin | **Yes**, as a script or one SQL line. Not a page |
| Rollout of the gate to Juffermans (7.14.0) and EmoVR (still `pan_outlook_pro` 19.0.1.0.17) | #126 | **Yes.** They are the customers |
| End-user doc for Connect to Pantalytics | docs/ | **Yes**, one page |
| Stripe checkout, portal, webhooks, prices | mail-pro-admin, phase 6 | No. Invoice from Odoo until the third paying customer |
| Download page, per-instance detail, revoke button, staff view | mail-pro-admin, phase 3 | No. Cloudpepper pulls git; revoke and staff views are psql |
| CRM `x_mailpro_*` fields via odoo_sync | phase 4 | No |
| Chatter door, customer timeline, Cc block | conversation-view.md | No. Sell the Inbox that exists |
| AI triage rung | mail-triage.md | No. Read `pan.mail.coverage` on a customer database first; it has never been run |
| Retention jobs and `revoke_installation` have no caller | mail-pro-admin #17 | No, but before the tables matter |

## Bugs to fix before the next customer rollout

- **#96**: every partner in `recipient_ids` lands in one To header, so
  followers at different customers see each other's addresses. An address
  leak on the default path. Fix before Juffermans, not after.
- **#149**: "Linked to nothing" is empty for everyone but the sender.
- **#140**: attachments invisible in the conversation pane.
- **#134**: Cloudpepper never runs `-u`. Not only on mailpro-dev: production
  Pantalytics sits on 12.5.0 in the database with 13.1.0 on disk right now.
  Until this is understood, every version bump is a manual Upgrade click on
  every instance, and the rollout runbook has to say so.

## Order

1. Fix the contract (#169): field name and the counts. One PR in the module,
   nothing on the server.
2. Fix #96.
3. Throttle and trial in the module, against the limit the entitlement now
   carries.
4. Juffermans staging, Juffermans prod, EmoVR, per #126.
5. Hand-set the first paid plan. Write the connect page in docs/.

Everything below the line in the table waits for a customer to ask.
