# Installation

## Requirements

- Odoo 19.0 Enterprise Edition
- The `cryptography` Python package (for encrypted credential storage)
- A mailbox on one of the supported providers:

| Provider | You also need |
|----------|---------------|
| Microsoft 365 | Azure AD admin access, to create an app registration |
| Google Workspace | Google Cloud access, to create an OAuth client |
| IMAP/SMTP | The server names, a login and a password |

## Install via Odoo.sh (Recommended)

### Step 1: Add as Git Submodule

1. In Odoo.sh, go to **Settings → Submodules**
2. Click **Add submodule**
3. Enter: `git@github.com:pantalytics/pan_mail_pro.git`
4. Copy the **Public Key** shown

### Step 2: Add Deploy Key in GitHub

1. Go to the pan_mail_pro repository on GitHub
2. **Settings → Deploy Keys → Add deploy key**
3. Paste the public key from Odoo.sh
4. Save

### Step 3: Deploy

Push to your Odoo.sh branch to trigger deployment:

```bash
git submodule add git@github.com:pantalytics/pan_mail_pro.git addons/pan_mail_pro
git commit -m "Add pan_mail_pro submodule"
git push
```

### Step 4: Install Module

1. Go to **Apps** in Odoo
2. Click **Update Apps List**
3. Search for "Mail Pro"
4. Click **Install**

## Next Steps

After installation, go to **Settings → Mail Pro** and pick your email provider.
Only that provider's setup steps are shown from then on:

- [Microsoft 365 Setup](azure-setup.md)
- [Google Workspace Setup](google-setup.md)
- [IMAP/SMTP Setup](imap-setup.md)
