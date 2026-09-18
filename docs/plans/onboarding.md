# Onboarding: from "found it" to "mail flows"

Status: **decided, not built.** Decided 2026-09-18. Owner: Rutger. The
installation guide ([getting-started/installation.md](../getting-started/installation.md))
already reads the way this plan says; the Microsoft part below is the work.

## The four steps, and what each one costs today

| Step | Today | Decided |
|---|---|---|
| 1. Register | A Pantalytics account, before anything else | Stays, but happens **inside Odoo**: Connect to Pantalytics is the sign-up. Nothing to do on our site first |
| 2. Get the module | Zip from a dashboard, behind a login | **Public GitHub repo.** Nothing behind a login; the Connect step is where we learn who the customer is |
| 3. Install | Deploy key, submodule, Apps list | Cannot be removed. One guide per hoster, nothing to copy except a URL |
| 4. Connect a mailbox | An Azure app registration per customer | **One Pantalytics-owned Microsoft app.** The admin consents once, every user presses Sign in |

The order the customer sees: install, press Connect to Pantalytics, press
Sign in with Microsoft. Two buttons after the install, no portals.

**Registration is not skipped.** PLG only works if usage can be traced back to
a person, and `sync_allowed()` already refuses incoming mail on a database
that has not connected. The device flow
(mail-pro-admin, `docs/plans/mail-pro-paid.md`) makes Connect the sign-up:
approving the code without a workspace creates one. What changes is where the
step sits, not whether it exists. Zitadel should offer "Continue with
Microsoft" and "Continue with Google", so the identity someone signs up with
is the one their mailbox lives on.

**The download is not gated.** The repo is public already. Knowing who
downloaded a zip is worth less than knowing who connected a database, and the
second number is the one the heartbeat gives us.

## The Microsoft app

Odoo Online ships its own Azure app and nobody there ever sees the portal;
self-hosted customers register one each, and that is the friction. We do what
Odoo Online does.

### Shape

- **One multi-tenant Entra app**, owned by Pantalytics, registered as a
  *public client* (the "Mobile and desktop applications" platform). Public
  client means **no client secret**: the module carries only the client id,
  and the token exchange is protected by PKCE instead of a secret. A secret
  in a public repo is no secret; PKCE needs none.
- **The redirect URI is ours**, one page on `app.mailpro.pantalytics.com`.
  Azure only redirects to a URI on the app's list, and a list of every
  customer's Odoo URL is the setup pain we are removing. The page reads the
  Odoo callback URL out of `state` and 302s the browser there with the code.
  It stores nothing and sees nothing usable: the code is worthless without
  the PKCE verifier, which only the customer's Odoo holds.
- **Odoo does the token exchange itself**, against Microsoft, with the
  verifier. Refresh runs the same way, straight from Odoo to Microsoft. Our
  site is a hop during sign-in and nothing afterwards: if it is down, nobody
  new can sign in and everybody connected keeps working. No token, refresh or
  access, ever touches a Pantalytics server.
- **Consent is one click.** The admin opens the tenant-wide consent link,
  `login.microsoftonline.com/common/adminconsent?client_id=...`, and grants
  the same delegated scopes `graph_client.py` asks for today. After that any
  user presses Sign in with Microsoft. Callback URL, tenant id, secret and the
  permissions list all disappear from the customer's side.
- **Bring your own app stays**, one level down on the provider form, for the
  IT department that requires it. It is the same code path with a different
  client id, a secret and a per-customer redirect, and it is the fallback if
  our app ever goes away.

### What it costs us, once

- **Publisher verification** on the app: an MPN id and a verified domain.
  Without it Microsoft blocks consent to a multi-tenant app from any tenant
  but our own.
- The bounce page in mail-pro-admin, and a `state` format the two sides agree
  on: the Odoo callback URL plus the nonce Odoo already uses for CSRF.
- In `graph_client.py`: PKCE on the authorize and token calls, a token exchange
  that sends no secret when the provider row is the Pantalytics app, and the
  `AUTH_URL` tenant as `organizations` instead of the customer's tenant id.
- The provider form: a "Sign in with Microsoft" default that needs no fields,
  with "Use my own app registration" under it.

### Not Google, not yet

The Gmail scopes the module needs are *restricted* scopes. A shared Google
app with those scopes needs Google's verification plus a yearly CASA security
assessment, paid. Google Workspace stays bring-your-own until there are
enough Google customers to make that a line item. IMAP/SMTP has no app at all.

## Installing: what the guide says and why

The repo is public, so every path is "point your hoster at the URL":

- **Odoo.sh**: a submodule over HTTPS. No deploy key, because there is nothing
  to authenticate against.
- **Cloudpepper**: attach the repo as a git addons source on branch `19.0`,
  install `pan_mail_pro` from it. Auto-requirements on, so `cryptography` is
  there before Odoo loads the module.
- **Your own git**: a submodule in the customer's addons repo, however they
  deploy that.
- **Odoo Online**: does not run third-party modules. The pricing page says so,
  so nobody spends a quarter of an hour finding out.

A hosted trial instance with the module pre-installed would remove step 3
for the people with nothing to install on. It is a second product to keep
running, so it waits for the first customer who asks.
