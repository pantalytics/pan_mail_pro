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

Odoo's own Outlook connector makes every customer register an Azure app, on
Odoo Online as much as on a server of their own. HubSpot and Pipedrive do not:
they own one app and the customer presses Sign in. We do the second thing.

Checked against Microsoft's documentation on 2026-09-18; the pages are listed
at the end of this section.

### Shape

- **One multi-tenant Entra app**, owned by Pantalytics, sign-in audience
  "Accounts in any organizational directory". No personal accounts: a mailbox
  we sync is a work mailbox, and the personal audience brings tighter
  redirect rules for nothing.
- **A public client, so no client secret.** The redirect URI is registered on
  the *Mobile and desktop applications* platform, which is what Microsoft
  calls a public client; a custom `https://` URI is allowed there. A public
  client must not send a secret when it redeems a code (Microsoft refuses one
  with AADSTS700025), and PKCE takes the secret's place. A secret in a public
  repo is no secret; PKCE needs none.
- **The redirect URI is ours**, one page on `app.mailpro.pantalytics.com`.
  Azure sends the code only to a URI on the app's list, the list holds 256 at
  most, and a list of every customer's Odoo URL is the setup pain we are
  removing. Microsoft's own answer to "many subdomains, one app" is a shared
  redirect plus `state`, with one warning: do not put the destination URL in
  `state`, or the page is an open redirector. So `state` carries the
  installation id and Odoo's CSRF nonce, and the page looks the Odoo URL up
  in `mailpro_installations`, where Connect to Pantalytics registered it. It
  redirects nowhere it does not already know, which also makes "Connect
  first" a mechanism rather than a rule. It stores nothing and sees nothing
  usable: the code is worthless without the PKCE verifier, which only the
  customer's Odoo holds.
- **Odoo does the token exchange itself**, against Microsoft, with the
  verifier and no secret. Refresh runs the same way, straight from Odoo to
  Microsoft. Our site is a hop during sign-in and nothing afterwards: if it is
  down, nobody new can sign in and everybody connected keeps working. No
  token, refresh or access, ever touches a Pantalytics server.
- **Consent, two paths, the cheap one first.** In a tenant with default
  settings a user may consent to mail permissions themselves, and Sign in
  with Microsoft is the whole step. Where the tenant reserves that for an
  admin (the "verified publishers, low impact only" policy leaves
  `Mail.ReadWrite` out, and many tenants disable user consent outright),
  Microsoft answers the sign-in with "needs admin approval", and only then
  does the provider form show the admin consent link:
  `login.microsoftonline.com/organizations/v2.0/adminconsent` with our
  client id, the scopes and the same redirect page. `organizations`, never
  `common`. Approval comes back to the redirect page as `admin_consent=True`,
  which it forwards to Odoo like a code.
- **Bring your own app stays**, one level down on the provider form, for the
  organisation that does not want a multi-tenant app in its directory. Same
  code path, a different client id, a secret and a per-customer redirect,
  and the fallback if our app ever goes away. The Pantalytics app is the
  proposed route and the default; the own-app form is one click further.

### The one cost of a public client

Microsoft revokes a public client's refresh token when the user changes
their password; a confidential client's survives. So a user who changes their
Microsoft password sees the "connect your mailbox" banner again and presses
one button. Bring-your-own keeps the old behaviour. Accepted: a password
change is rare and the banner already exists.

Refresh tokens otherwise live 90 days from their last use and renew on every
use. The daily sync keeps them alive.

### Registering with Microsoft, once

Publisher verification needs a Partner Center account, and Partner Center
takes days where the app registration takes minutes. Start here.

1. **Enrol in the Microsoft AI Cloud Partner Program** (the programme
   formerly called MPN) at partner.microsoft.com, *Become a partner*.
   Membership itself is free; the paid packages (Partner Launch, Success
   Core, Solutions Partner) are benefit bundles we do not need. Sign in with
   a work account in the Pantalytics Entra tenant as a **Global
   Administrator**, never a personal Microsoft account; the enrolling person
   must be allowed to sign the programme agreement for the company.
2. **Legal entity.** Legal name, address and primary contact exactly as in
   the trade register (KvK), plus the KvK number as Registration ID. A
   spelling difference is the most common rejection.
3. **Verification, three to five business days.** Four checks: the contact
   email, the identity of one user (government id, name must match the
   account), employment (a domain document from the registrar showing
   `pantalytics.com`, its owner and dates), and the business itself (KvK
   extract). Progress and document uploads live under Account settings,
   Legal info, Verification summary. Enrolment is active only when the
   status reads *Authorized*.
4. **Partner One ID.** Account settings, Identifiers, the Microsoft AI Cloud
   Partner Program tab. Two ids appear; the verification step wants the
   **partner global account** (PGA), not the location.
5. **Publisher domain on the tenant.** If `pantalytics.com` is already a
   verified domain of the tenant (it is, when Microsoft 365 mail runs on it),
   the app's Branding page lets you pick it. Otherwise host
   `https://pantalytics.com/.well-known/microsoft-identity-association.json`
   with the app's client id and press *Verify and save domain*; the file can
   go once verified.

Then the app, below, and the verification badge as its last step.

### Configuring the app, step by step

Once, in the Pantalytics tenant. Roles needed: Application Administrator in
Entra, admin on the Partner Center account, signed in with MFA.

1. **Register.** Entra admin center, App registrations, New registration.
   Name `Mail Pro by Pantalytics`. Supported account types: *Accounts in any
   organizational directory (Any Microsoft Entra ID tenant, Multitenant)*.
   Leave the redirect URI empty here.
2. **Redirect.** Authentication, Add a platform, *Mobile and desktop
   applications*, Custom redirect URIs:
   `https://app.mailpro.pantalytics.com/oauth/microsoft`. Do not add a Web
   platform and do not create a client secret; either turns the app into a
   confidential client for that redirect. *Allow public client flows* can
   stay off: it is for flows without a redirect (device code, ROPC).
3. **Permissions.** API permissions, Microsoft Graph, Delegated:
   `User.Read`, `Mail.ReadWrite`, `Mail.Send`, `Mail.ReadWrite.Shared`,
   `Mail.Send.Shared`, `offline_access`. The same list `graph_client.py`
   requests today, so the admin consent link with `/.default` covers exactly
   these. Grant admin consent for the Pantalytics tenant to test against our
   own mailboxes.
4. **Branding.** Branding & properties: logo, home page, terms of service and
   privacy statement URLs, and **publisher domain** `pantalytics.com`. The
   consent screen shows all of it, and step 5 requires the domain.
5. **Publisher verification.** Free, minutes once the prerequisites hold.
   Prerequisites: a Microsoft AI Cloud Partner Program (formerly MPN) account
   that has completed Partner Center's verification, its **Partner One ID**
   for the partner global account (not a location id), the app registered by
   a work account in a tenant tied to that partner account, and the
   verification email's domain equal to the publisher domain or a
   DNS-verified custom domain on the tenant. Then Branding & properties, *Add
   Partner ID to verify publisher*, enter the id, Verify and save. Without
   the badge, users in other tenants cannot consent to a multi-tenant app
   registered after November 2020; admins still can. So the badge is what
   makes the no-admin path exist.
6. **Test from mailpro-dev.** The redirect is ours, not the instance's, so
   the same app serves dev, staging and every customer without another
   redirect URI. A staging copy of the redirect page would need one; there
   are 255 left.

### What changes in the code

- `graph_client.py`: PKCE (`code_challenge` S256 on authorize,
  `code_verifier` on the token call); the verifier stored next to
  `x_pan_mail_oauth_state` on the user for the one round trip; no
  `client_secret` on either token call when the provider row is the
  Pantalytics app; `organizations` as the tenant in both URLs.
- `pan.mail.provider`: a mode, `pantalytics` or `own`. The first has a
  constant client id and no fields; the second is the form as it is today.
- `controllers/main.py`: the callback also accepts `admin_consent=True` and
  records that the tenant consented.
- `pan.mail.license`: the heartbeat carries `web.base.url`, so a moved
  database moves its redirect with it.
- mail-pro-admin: `GET /oauth/microsoft`, the redirect page. Reads
  `state`, looks the installation up, 302s to
  `<odoo_url>/microsoft_oauth/callback` with the query string intact. Unknown
  installation: a plain error page, no redirect. No session, no storage.

### Not Google, not yet

The Gmail scopes the module needs are *restricted* scopes. A shared Google
app with those scopes needs Google's verification plus a yearly CASA security
assessment, paid. Google Workspace stays bring-your-own until there are
enough Google customers to make that a line item. IMAP/SMTP has no app at all.

### Sources

- [Redirect URI restrictions](https://learn.microsoft.com/en-us/entra/identity-platform/reply-url):
  256 URIs, https only, the shared redirect plus `state` pattern and its
  open-redirect warning
- [Auth code flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow):
  PKCE parameters, "public clients must not use secrets", `organizations`
- [Adding a redirect URI](https://learn.microsoft.com/en-us/entra/identity-platform/how-to-add-redirect-uri):
  custom URIs on the Mobile and desktop platform
- [Admin consent endpoint](https://learn.microsoft.com/en-us/entra/identity-platform/v2-admin-consent)
- [Refresh tokens](https://learn.microsoft.com/en-us/entra/identity-platform/refresh-tokens):
  lifetimes, and the revocation table that separates public from
  confidential clients
- [Publisher verification](https://learn.microsoft.com/en-us/entra/identity-platform/publisher-verification-overview)
  and [how to mark the app](https://learn.microsoft.com/en-us/entra/identity-platform/mark-app-as-publisher-verified)
- [User consent settings](https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/configure-user-consent):
  the default policies and what "low impact" leaves out
- [Partner Center enrolment](https://learn.microsoft.com/en-us/partner-center/enroll/partner-center-enroll-overview),
  [the verification process](https://learn.microsoft.com/en-us/partner-center/enroll/understand-the-verification-process)
  and [membership offers](https://learn.microsoft.com/en-us/partner-center/membership/mpn-overview)
  (what is free and what is a paid bundle)
- [Publisher domain](https://learn.microsoft.com/en-us/entra/identity-platform/howto-configure-publisher-domain):
  the verified-domain pick and the JSON file alternative
- [Odoo's own Azure guide](https://www.odoo.com/documentation/19.0/applications/general/email_communication/azure_oauth.html):
  one app per customer, on every hosting

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
