# Installation

Mail Pro is an Odoo module. You add it to your Odoo the way your hoster adds
any module, then connect it to your Pantalytics account from inside Odoo.
Nothing to download, nothing to sign up for first.

## Requirements

- Odoo 19.0, Community or Enterprise (routing to Helpdesk teams needs Enterprise)
- The `cryptography` Python package (for encrypted credential storage).
  Odoo.sh and Cloudpepper install it for you; on your own server,
  `pip install cryptography` once
- A mailbox on one of the supported providers:

| Provider | You also need |
|----------|---------------|
| Microsoft 365 | Azure AD admin access, to create an app registration |
| Google Workspace | Google Cloud access, to create an OAuth client |
| IMAP/SMTP | The server names, a login and a password |

**Odoo Online** (the `*.odoo.com` plans) cannot run modules from outside Odoo's
own store, so Mail Pro does not run there. Odoo.sh, Cloudpepper and any server
you control all work.

The source is public: **https://github.com/pantalytics/pan_mail_pro**, branch
`19.0`. No account, key or token is needed to fetch it.

## Odoo.sh

1. In Odoo.sh, open your project, then **Settings → Submodules**
2. Click **Add submodule** and enter
   `https://github.com/pantalytics/pan_mail_pro.git`, branch `19.0`
3. Odoo.sh commits the submodule to your branch and rebuilds it

Or from your own clone of the project:

```bash
git submodule add -b 19.0 https://github.com/pantalytics/pan_mail_pro.git addons/pan_mail_pro
git commit -m "Add Mail Pro"
git push
```

Odoo.sh asks for a deploy key only for private repositories. This one is
public, so skip that step if it shows one.

To update later: `git submodule update --remote addons/pan_mail_pro`, commit,
push. Odoo.sh runs the module upgrade on the rebuild.

## Cloudpepper

1. Open the instance, then its **Modules** (git addons) page
2. **Add repository**: `https://github.com/pantalytics/pan_mail_pro.git`,
   branch `19.0`
3. Leave **install requirements automatically** on; it installs
   `cryptography` before Odoo loads the module
4. Tick `pan_mail_pro` to install it right away, or install it from Apps
   afterwards (next section)

Cloudpepper can re-pull the repository on every push to `19.0` (the webhook
option) and run the module upgrade at the same time (auto-upgrade). Both on
means a Mail Pro release lands on your instance without you doing anything;
on a production instance you may prefer to upgrade from Apps yourself, on a
day you choose.

## Your own server, or your own addons repository

Add the module to a directory on Odoo's `addons_path`, or as a submodule of the
repository you deploy from:

```bash
cd /path/to/your/addons
git clone -b 19.0 https://github.com/pantalytics/pan_mail_pro.git
# or, inside your own addons repository:
git submodule add -b 19.0 https://github.com/pantalytics/pan_mail_pro.git pan_mail_pro
```

Make sure the directory that holds `pan_mail_pro` is on `addons_path` in
`odoo.conf`, install `cryptography` in Odoo's Python environment, and restart
Odoo.

To update later: `git pull` (or update the submodule), restart Odoo, then
**Apps → Mail Pro → Upgrade**. Odoo only applies a new version when you upgrade
the module; a restart alone loads the new code but not its views or data.

## Install the module in Odoo

Skip this if Cloudpepper already installed it for you.

1. Go to **Apps** and, in developer mode, click **Update Apps List**
2. Search for "Mail Pro"
3. Click **Install**

## Connect to Pantalytics

The first thing Mail Pro asks for is a Pantalytics account, and it asks from
inside Odoo. The setup checklist opens once the instance is connected.

1. Go to **Settings → Mail Pro** and press **Connect to Pantalytics**
2. A Pantalytics tab opens with a short code in it; Odoo shows the same code
   so you can check it is your instance asking
3. Sign in there, or sign up if you have no account yet, and approve
4. Back in Odoo, press **Check Approval** if the page has not noticed on its own

That is the whole registration. No key to copy, nothing to paste. The database
checks in with Pantalytics once a day to keep its licence current; what it
sends is counts only, never an address, a subject or a message. See
[Security](../security.md).

## Next Steps

Step 1 of the checklist on **Settings → Mail Pro** opens the provider form;
pick yours and follow its guide:

- [Microsoft 365 Setup](azure-setup.md)
- [Google Workspace Setup](google-setup.md)
- [IMAP/SMTP Setup](imap-setup.md)
