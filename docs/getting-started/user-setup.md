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

1. Click your **profile picture** (top right) → **My Profile**
2. Go to the **Mail Pro** tab
3. Click **Connect Mailbox**
4. Sign in with your Microsoft 365 or Google Workspace account
5. Grant the requested permissions

A personal mailbox is created for the address you signed in with, and set as
your **Send from**.

An administrator can invite everybody at once with **Send Mail Pro Invite** on
the Users list (Settings → Mail Pro → Users).

## Check it works

On **My Profile → Mail Pro**, press **Send Test Email**. It sends a message
from your own mailbox to your own address, so a failure names the problem
before a customer finds it. Every mailbox form has the same button.

## Set your default mailbox

1. Go to **My Profile → Mail Pro**
2. Pick a mailbox in **Send from**
3. Save

That mailbox is pre-selected in the composer. You can still change it per email.

## Sending emails

1. Open any email composer (from CRM, Helpdesk, a sales order)
2. Use the **Send From** dropdown to pick a mailbox
3. Compose and send

You see your own personal mailbox and every shared mailbox. On Microsoft 365 a
shared mailbox also needs SendAs permission on the address.

## Disconnecting

1. Go to **My Profile → Mail Pro**
2. Click **Disconnect** and confirm

This removes your stored credentials. Odoo stops sending and receiving for that
account until you connect again. Only you, or an administrator, can change your
connection.
