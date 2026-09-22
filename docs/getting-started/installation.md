# Installation

Two steps: put the module on your Odoo, then connect it to your Pantalytics
account. If you have not created that account yet, do it first at
<a href="https://app.mailpro.pantalytics.com/start" target="_blank">app.mailpro.pantalytics.com/start</a>:
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
branch `19.0`, under the Elastic License 2.0. It is not on the Odoo App Store.

## Step 1: Add the module to your Odoo

Pick the host you run.

### Odoo.sh

1. In Odoo.sh, go to **Settings → Submodules** and click **Add submodule**
2. Enter `git@github.com:pantalytics/pan_mail_pro.git`, branch `19.0`, and
   copy the **Public Key** shown
3. The repository is public, so the key is only needed if your Odoo.sh project
   pulls over SSH. If it asks for one: send it to support@pantalytics.com and
   we add it as a read-only deploy key
4. Or from your own repository, on your machine:

```bash
git submodule add -b 19.0 https://github.com/pantalytics/pan_mail_pro addons/pan_mail_pro
git commit -m "Add pan_mail_pro"
git push
```

Odoo.sh builds the branch and restarts.

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

1. Clone the branch into a folder on Odoo's `addons_path`:

```bash
cd /path/to/your/addons
git clone -b 19.0 https://github.com/pantalytics/pan_mail_pro
```

2. Make sure Odoo's Python has `cryptography`: `pip install cryptography`
   (the official `odoo:19.0` Docker image already has it)
3. Restart Odoo

In Docker, mount the folder that holds the clone as an extra addons directory,
for example `./addons:/mnt/extra-addons`, and put `/mnt/extra-addons` in
`addons_path` in `odoo.conf`.

### Then, on every host

1. Go to **Apps** in Odoo
2. Click **Update Apps List**
3. Search for "Mail Pro"
4. Click **Install**

## Step 2: Connect to Pantalytics

Go to **Settings → Mail Pro** and press **Connect to Pantalytics**. A tab opens
on `app.mailpro.pantalytics.com` with a code already filled in; sign in, check
that the page names this Odoo, and approve. The full walk-through, and what
happens if the tab does not open, is in
[Connect to Pantalytics](connect-pantalytics.md).

Free covers 100 mails a day per Odoo instance. Pro (1,000 a day) and Max
(5,000 a day) are one button on the Billing page of your Pantalytics
workspace.

## Next Steps

Once connected, the Settings page shows a checklist of three steps. Step 1
opens the provider form; pick yours and follow its guide:

- [Microsoft 365 Setup](azure-setup.md)
- [Google Workspace Setup](google-setup.md)
- [IMAP/SMTP Setup](imap-setup.md)
