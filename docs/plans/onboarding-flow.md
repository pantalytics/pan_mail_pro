# Onboarding: from the beta mail to the first mail on a record

Status: **plan, 2026-10-20.** Nothing here is built. Written after walking the
whole beta install on a real Odoo.sh project. Install and Connect went fine,
then the provider form asked for a Client ID and secret with nothing on screen
to say where they come from.

## The result we steer to

One moment counts: **a customer's mail shows up on the right Odoo record,
in the Inbox, without anyone filing it.** Every step before that is cost, and
every step should say how far away that moment still is. Target: one sitting,
under thirty minutes, for an admin who runs their own Odoo.sh.

## The line: five stations, one open at a time

The shape MCP Pro's Home already uses (`home_journey.py` in
odoo-mcp-pro-admin): a metro line, stations done / now / next, one button on
the station you stand on, the rest folded.

| # | Station | Done when | Where it is drawn |
|---|---------|-----------|-------------------|
| 1 | **Spot claimed** | the Pantalytics account exists | drawn done: anyone who reads the line has one |
| 2 | **Mail Pro on your Odoo** | the module is installed and connected to Pantalytics | the panel's Mail Pro page, because Odoo has no module to draw it in yet |
| 3 | **Sign-in app** | the provider row is complete and verified | Settings → Mail Pro |
| 4 | **Your mailbox** | the admin signed in with their own address | Settings → Mail Pro |
| 5 | **First mail on a record** | the first incoming mail landed on a record that is not a contact | Settings → Mail Pro, with a link to that record |

The panel draws the same five from the heartbeat, which already carries the
setup answers within the minute (`_report_setup_if_changed`). Both places read
one state, so they cannot disagree.

Today's four checklist steps map onto 1 to 4. Internal domains and the
notification mailbox stop being stations, see below. Station 5 is new and is
the one that matters.

## Fewer decisions: two steps become defaults

- **Internal domains.** Pre-filled with the admin's own domain (their login,
  or the address they sign in with at station 4), shown as "We treat
  `acme.com` as internal", with Change one level deeper. The fail-closed gate
  stays: the domain exists, it was only proposed instead of asked.
- **Notification mailbox.** The first mailbox that connects becomes it,
  unless the admin picks another. Odoo's system mail keeps flowing the way it
  does today until then.

Dropped case: a company whose admin signs in from a domain that is not the
company's. They correct the proposal; we do not build a question for them.

## Station 3, the hard one, made shorter

Until we own the Microsoft app ([#345](https://github.com/pantalytics/pan_mail_pro/issues/345),
waiting on Daniel), the customer creates the sign-in app themselves. Three
things make it shorter:

1. **The callback URL first.** The console asks for it while you create the
   app, so the form shows it before the fields you only have afterwards.
2. **The guide one click away** (done in #343), and on the station itself the
   three things you will paste, so the admin knows the end before starting.
3. **Verify on save** already exists. The station turns green the moment it
   passes, and the line moves on by itself.

## People, for the first hundred

With a hundred beta spots, the cheapest way to lower churn is a person.
The panel sees which station each installation is stuck on. Two rules:

- **Stuck on a station for a day:** one short mail from Rutger or Daniel,
  naming the station and offering fifteen minutes to do it together.
- **Station 5 reached:** a mail asking what they would pay. That answers the
  rock's open question ("betaalbereidheid").

No automation beyond a list in the panel and a daily look at it. Built when
the hundred do not fit on one screen.

## Measuring

One PostHog event per station reached (`onboarding_station`, with the station
number and the installation id, no names), so the funnel shows where people
drop off. The heartbeat already sends the answers, so this is the panel
capturing what it receives, not new data leaving the customer's Odoo.

## Order

1. Smart defaults for domains and the notification mailbox (two stations fewer).
2. The stuck list in the panel, and the two mails, sent by hand.
3. Station 5 and the metro line in Settings.
4. The Pantalytics-owned Microsoft app (#345), once Daniel approves.
