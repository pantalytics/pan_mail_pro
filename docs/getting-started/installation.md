# Installation

Three steps and about ten minutes: add the module to your Odoo, install it,
and connect it to your Pantalytics account.

> 💡 You need Odoo 19.0, Community or Enterprise, on Odoo.sh, Cloudpepper or
> your own server. Odoo Online does not allow extra modules.

## Step 1: Add the module from GitHub

Mail Pro lives at
<a href="https://github.com/pantalytics/pan_mail_pro" target="_blank">github.com/pantalytics/pan_mail_pro</a>,
branch `19.0`. Pick your host.

### Odoo.sh

Start on a staging branch, not on production.

1. Open the branch and click **Submodule** at the top right
2. Repository `git@github.com:pantalytics/pan_mail_pro.git`, branch `19.0`,
   path `addons/pan_mail_pro`
3. Save. Odoo.sh builds the branch and restarts

> 💡 The repository is public, so you need no deploy key. Odoo explains
> submodules in its own guide:
> <a href="https://www.odoo.com/documentation/19.0/administration/odoo_sh/advanced/submodules.html" target="_blank">Odoo.sh submodules</a>.

Rather use the command line:

```bash
git submodule add -b 19.0 https://github.com/pantalytics/pan_mail_pro addons/pan_mail_pro
git commit -m "Add Mail Pro" && git push
```

### Cloudpepper

Your instance → **Modules → Git** → add
`https://github.com/pantalytics/pan_mail_pro`, branch `19.0` → **Restart**.
Leave auto upgrade off on production.

### Your own server or Docker

```bash
cd /opt/odoo
git clone -b 19.0 https://github.com/pantalytics/pan_mail_pro
# odoo.conf: addons_path = ...,/opt/odoo/pan_mail_pro
```

Restart Odoo. Odoo's Python needs `cryptography`; the official `odoo:19.0`
image has it.

## Step 2: Install it in Odoo

**Apps → Update Apps List** → search "Mail Pro" → **Install**.

> ⚠️ On Odoo.sh, merging into production does not install the module there.
> Open **Apps** on production and install Mail Pro once more.

## Step 3: Connect to Pantalytics

**Settings → Mail Pro → Connect to Pantalytics**. A tab opens with a code
filled in: sign in, check it names this Odoo, approve. No account yet? Create
one first at
<a href="https://mcp.pantalytics.com/admin/mail-pro" target="_blank">mcp.pantalytics.com/admin/mail-pro</a>.
Stuck? See [Connect to Pantalytics](connect-pantalytics.md).

> ✅ Done. The Settings page now walks you through the mailbox. Pick your
> provider: [Microsoft 365](azure-setup.md), [Google Workspace](google-setup.md)
> or [IMAP/SMTP](imap-setup.md).

## Updating to a new release

- **Odoo.sh or git:** `git submodule update --remote addons/pan_mail_pro`,
  commit, push. A submodule never moves on its own, so you decide when.
- **Cloudpepper:** pulls the release and restarts by itself.

A release that adds fields or screens also needs **Apps → Mail Pro →
Upgrade**. The release notes say when.
