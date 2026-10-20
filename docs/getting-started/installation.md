# Installation

Two steps: put the module on your Odoo, then connect it to your Pantalytics
account. If you have not created that account yet, do it first at
<a href="https://mcp.pantalytics.com/admin/mail-pro" target="_blank">mcp.pantalytics.com/admin/mail-pro</a>:
it takes a work email address and a minute, and the page you land on repeats
the steps below for the host you run.

## Requirements

- Odoo 19.0, Community or Enterprise (routing to Helpdesk teams needs Enterprise)
- The `cryptography` Python package (for encrypted credential storage). Odoo.sh
  and Cloudpepper have it; on your own server, see below
- A mailbox on one of the supported providers:

| Provider | You also need |
|----------|---------------|
| Microsoft 365 | Azure AD admin access, to create an app registration |
| Google Workspace | Google Cloud access, to create an OAuth client |
| IMAP/SMTP | The server names, a login and a password |

Mail Pro is distributed from its GitHub repository,
<a href="https://github.com/pantalytics/pan_mail_pro" target="_blank">github.com/pantalytics/pan_mail_pro</a>,
branch `19.0`, under the Elastic License 2.0, and listed free on the Odoo App Store.

## Step 1: Add the module to your Odoo

Pick the host you run.

### Odoo.sh

Start on a staging or development branch, not on production.

1. In your Odoo.sh project, open that branch and click **Submodule** at the
   top right
2. Repository: `git@github.com:pantalytics/pan_mail_pro.git`. Branch: `19.0`.
   Path: `addons/pan_mail_pro`
3. Save. Odoo.sh commits the submodule to your branch, builds it and restarts

The repository is public, so you do not need a deploy key. **Settings →
Submodules** is only for private repositories; you can skip it.

Prefer git? From your own clone of the project:

```bash
git submodule add -b 19.0 https://github.com/pantalytics/pan_mail_pro addons/pan_mail_pro
git commit -m "Add Mail Pro"
git push
```

Install it on that branch (see *Then, on every host* below), check it works,
and merge the branch into production. Odoo.sh does not install a new module
on merge: open **Apps** on production and install Mail Pro there too.

A new release: `git submodule update --remote addons/pan_mail_pro`, commit,
push. Odoo.sh never moves a submodule on its own, so you decide when an
update lands.

### Cloudpepper

1. Open your instance in the Cloudpepper dashboard and go to **Modules → Git**
2. Add the repository `https://github.com/pantalytics/pan_mail_pro`, branch
   `19.0`. Leave **webhook** on so a new release is pulled on push; leave
   **auto upgrade** off on production, so an upgrade is a step you take
3. **Restart** the instance

Later releases: Cloudpepper pulls the code and restarts, which is enough for
most changes. A release that adds fields or views also needs **Apps → Mail Pro
→ Upgrade**; the release notes say so when it does.

### Your own server or Docker

1. Clone the branch and add the clone itself to Odoo's `addons_path`. The
   module is the `pan_mail_pro/` folder inside it:

```bash
cd /opt/odoo
git clone -b 19.0 https://github.com/pantalytics/pan_mail_pro
# odoo.conf: addons_path = ...,/opt/odoo/pan_mail_pro
```

2. Make sure Odoo's Python has `cryptography`: `pip install cryptography`
   (the official `odoo:19.0` Docker image already has it)
3. Restart Odoo

In Docker, mount the clone as an extra addons directory, for example
`./pan_mail_pro:/mnt/extra-addons`, and put `/mnt/extra-addons` in
`addons_path` in `odoo.conf`.

### Then, on every host

1. Go to **Apps** in Odoo
2. Click **Update Apps List**
3. Search for "Mail Pro"
4. Click **Install**

## Step 2: Connect to Pantalytics

Go to **Settings → Mail Pro** and press **Connect to Pantalytics**. A tab opens
on `mcp.pantalytics.com` with a code already filled in; sign in, check
that the page names this Odoo, and approve. The full walk-through, and what
happens if the tab does not open, is in
[Connect to Pantalytics](connect-pantalytics.md).

Free covers 100 mails a day per Odoo instance. Pro (1,000 a day) and Max
(5,000 a day) are one button on the Billing page of your Pantalytics
workspace.

## Next Steps

The Settings page is a checklist of four steps, and connecting was step 1.
Step 2 opens the provider form; pick yours and follow its guide:

- [Microsoft 365 Setup](azure-setup.md)
- [Google Workspace Setup](google-setup.md)
- [IMAP/SMTP Setup](imap-setup.md)
