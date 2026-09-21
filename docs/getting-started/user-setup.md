# User Setup

Once an administrator has set up the provider, each user connects their own
mailbox. What that means depends on the provider:

| Provider | Who connects | How |
|----------|--------------|-----|
| Microsoft 365 | The user | Signs in on the Microsoft consent screen |
| Google Workspace | The user | Signs in on the Google consent screen |
| IMAP/SMTP | An administrator | Types the server, login and password on the account |

On IMAP/SMTP there is no consent screen and no button for the user to press.
See [IMAP/SMTP Setup](imap-setup.md).

## The banner

Until you connect, a banner sits above every screen: *"Your mailbox is not
connected yet."* Its **Connect my mailbox** button drops you straight on the
consent screen. **Hide** dismisses it until your next sign-in.

The banner only appears where the button would work: an internal user who is
not connected, on a database whose provider is set up and uses OAuth, and never
on a staging copy.

## Connect your mailbox

1. Click your **profile picture** (top right) → **My Preferences**
2. Click **Connect Mailbox**
3. Sign in with your Microsoft 365 or Google Workspace account
4. Grant the requested permissions
5. Choose what Odoo may read from your mailbox, on the page that comes back

A personal mailbox is created for the address you signed in with, and set as
your **Send from**. The level you pick is yours: an administrator can lower it
or disconnect the mailbox, and only you can let Odoo read more. Mail that has
already landed on a record stays there when you lower it.

An administrator can invite everybody at once with **Send Mail Pro Invite** on
the Users list (Settings → Mail Pro → Users).

## Check it works

On **My Preferences**, press **Send Test Email**. It sends a message
from your own mailbox to your own address, so a failure names the problem
before a customer finds it. Every mailbox form has the same button.

## Set your default mailbox

1. Go to **My Preferences**
2. Pick a mailbox in **Send from**
3. Save

That mailbox is pre-selected in the composer. You can still change it per email.

## Sending emails

1. Open any email composer (from CRM, Helpdesk, a sales order)
2. Use the **Send From** dropdown to pick a mailbox
3. Compose and send

You see your own personal mailbox and every shared mailbox. On Microsoft 365 a
shared mailbox also needs SendAs permission on the address.

## Change what Odoo reads

1. Go to **My Preferences**
2. Pick a level in **Odoo reads**
3. Save

## Disconnecting

1. Go to **My Preferences**
2. Click **Disconnect** and confirm

This removes your stored credentials. Odoo stops sending and receiving for that
account until you connect again. Only you, or an administrator, can change your
connection.
